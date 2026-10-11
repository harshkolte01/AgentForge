"use client";
import { Children, createContext, isValidElement, memo, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { CodeBlock } from "@/features/sessions/code-block";
import { splitAssistantContent } from "@/features/sessions/assistant-content";
import { splitMarkdownBlocks } from "@/lib/markdown-blocks";
import { stabilizeMarkdown } from "@/lib/markdown-stabilize";

function safeLink(value: string) {
  if (value.startsWith("#") || (value.startsWith("/") && !value.startsWith("//") && !value.includes("\\"))) return value;
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : ""; }
  catch { return ""; }
}

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return textOf(node.props.children);
  return "";
}

/** Whether the reply is still being written: its code blocks stay plain text until it is complete. */
const StreamingContext = createContext(false);

function Fence({ children }: { children?: ReactNode }) {
  const streaming = useContext(StreamingContext);
  const child = Children.toArray(children).find((node): node is ReactElement<{ className?: string; children?: ReactNode }> => isValidElement(node));
  const code = textOf(child?.props.children).replace(/\n$/, "");
  const language = /language-([\w+#.-]+)/.exec(child?.props.className ?? "")?.[1];
  return <CodeBlock code={code} language={language} streaming={streaming} />;
}

const components: Components = {
  p: ({ children }) => <p className="whitespace-pre-line">{children}</p>,
  h1: ({ children }) => <h2 className="mt-7 text-lg font-semibold leading-snug tracking-tight">{children}</h2>,
  h2: ({ children }) => <h3 className="mt-6 text-base font-semibold leading-snug tracking-tight">{children}</h3>,
  h3: ({ children }) => <h4 className="mt-5 text-[15px] font-semibold leading-snug">{children}</h4>,
  h4: ({ children }) => <h5 className="mt-4 text-[15px] font-semibold leading-snug">{children}</h5>,
  ul: ({ children }) => <ul className="list-disc space-y-1 pl-6 marker:text-muted-foreground [&_ol]:mt-1.5 [&_ul]:mt-1.5">{children}</ul>,
  ol: ({ children }) => <ol className="list-decimal space-y-1 pl-6 marker:text-muted-foreground [&_ol]:mt-1.5 [&_ul]:mt-1.5">{children}</ol>,
  li: ({ children }) => <li className="pl-1">{children}</li>,
  pre: ({ children }) => <Fence>{children}</Fence>,
  code: ({ children }) => <code className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[0.875em]">{children}</code>,
  blockquote: ({ children }) => <blockquote className="border-l-2 pl-4 text-muted-foreground">{children}</blockquote>,
  hr: () => <hr className="my-6" />,
  table: ({ children }) => <div className="my-4 max-w-full overflow-x-auto rounded-xl border" tabIndex={0} role="region" aria-label="Table"><table className="w-full border-collapse text-left text-sm">{children}</table></div>,
  thead: ({ children }) => <thead className="bg-muted/50">{children}</thead>,
  th: ({ children }) => <th className="border-b px-3.5 py-2 font-medium">{children}</th>,
  td: ({ children }) => <td className="border-b px-3.5 py-2 align-top [tr:last-child_&]:border-b-0">{children}</td>,
  img: ({ alt }) => alt ? <span>{alt}</span> : null,
  a: ({ href, children }) => href ? <a href={href} className="rounded font-medium underline decoration-foreground/30 underline-offset-4 transition-colors outline-none hover:decoration-foreground focus-visible:ring-2 focus-visible:ring-ring/60" {...(href.startsWith("http") ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{children}</a> : <span>{children}</span>,
};

const plugins = [remarkGfm];

/** About this many frames to catch up with whatever has arrived, so the reveal never falls behind the model. */
const CATCH_UP_FRAMES = 10;
const reducedMotion = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/**
 * Streamed text arrives in bursts (a few lines every ~150 ms); shown as it lands, the reply and the view that follows it jump by
 * several lines at a time. While `streaming`, this reveals what has arrived over the next few frames instead, a little per
 * frame, at a speed proportional to the backlog. Finished text, replaced text and reduced motion are shown as they are.
 */
function useRevealed(content: string, streaming: boolean): string {
  const [shown, setShown] = useState(content);
  const target = useRef(content);
  useLayoutEffect(() => { target.current = content; }, [content]);
  const pacing = streaming && !reducedMotion();
  useEffect(() => {
    if (!pacing) return;
    let frame = 0;
    let odd = false;
    const step = () => {
      frame = requestAnimationFrame(step);
      // Every other frame (about 30 times a second) is smooth to the eye and halves the Markdown work.
      odd = !odd;
      if (odd) return;
      setShown((current) => {
        const goal = target.current;
        if (!goal.startsWith(current)) return goal; // replaced, not extended: never animate unrelated text
        if (current.length >= goal.length) return current;
        let next = current.length + Math.max(3, Math.ceil((goal.length - current.length) / CATCH_UP_FRAMES));
        if (next < goal.length && /[\uDC00-\uDFFF]/.test(goal[next])) next += 1; // never split a surrogate pair
        return goal.slice(0, next);
      });
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [pacing]);
  if (!pacing) return content;
  // Shrunk or replaced since the last frame: show the new text at once rather than a stale prefix.
  return content.startsWith(shown) ? shown : content;
}

/** One top-level block, parsed only when its own text changes: while streaming, only the block still being written re-parses. */
const MarkdownBlock = memo(function MarkdownBlock({ text }: { text: string }) {
  return <Markdown skipHtml urlTransform={safeLink} remarkPlugins={plugins} components={components}>{text}</Markdown>;
});

/**
 * A reply, rendered as inert Markdown (raw HTML is skipped, links are restricted). While `streaming` it is the live text so far:
 * incomplete fences, tables, links and emphasis are stabilized so the growing prefix always looks like the finished text, and
 * `onProgress` fires as it grows. Legacy leading reasoning tags are never shown.
 */
export const AssistantMessage = memo(function AssistantMessage({ content: arrived, streaming = false, onProgress }: { content: string; streaming?: boolean; onProgress?: () => void }) {
  const content = useRevealed(arrived, streaming);
  const progress = useRef(onProgress);
  useEffect(() => { progress.current = onProgress; }, [onProgress]);
  useEffect(() => { if (streaming) progress.current?.(); }, [content, streaming]);
  const { answer } = splitAssistantContent(content, streaming);
  const text = useMemo(() => stabilizeMarkdown(answer, { streaming }), [answer, streaming]);
  // Blocks render as siblings (no wrappers), so spacing and the first child's margin are exactly those of one whole document.
  const blocks = useMemo(() => splitMarkdownBlocks(text), [text]);
  return <div className="min-w-0 space-y-4 break-words text-[15px] leading-7 [&>*:first-child]:mt-0" data-streaming={streaming ? "true" : undefined}>
    {blocks.length ? <StreamingContext value={streaming}>{blocks.map((block, index) => <MarkdownBlock key={index} text={block} />)}</StreamingContext> : null}
  </div>;
});
