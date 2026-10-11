"use client";
import { Children, createContext, isValidElement, memo, useContext, useEffect, useMemo, useRef, type ReactElement, type ReactNode } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { CodeBlock } from "@/features/sessions/code-block";
import { splitAssistantContent } from "@/features/sessions/assistant-content";
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
  a: ({ href, children }) => href ? <a href={href} className="rounded font-medium underline decoration-foreground/30 underline-offset-4 transition-colors hover:decoration-foreground focus-visible:outline-2" {...(href.startsWith("http") ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{children}</a> : <span>{children}</span>,
};

const plugins = [remarkGfm];

/**
 * A reply, rendered as inert Markdown (raw HTML is skipped, links are restricted). While `streaming` it is the live text so far:
 * incomplete fences, tables, links and emphasis are stabilized so the growing prefix always looks like the finished text, and
 * `onProgress` fires as it grows. Legacy leading reasoning tags are never shown.
 */
export const AssistantMessage = memo(function AssistantMessage({ content, streaming = false, onProgress }: { content: string; streaming?: boolean; onProgress?: () => void }) {
  const progress = useRef(onProgress);
  useEffect(() => { progress.current = onProgress; }, [onProgress]);
  useEffect(() => { if (streaming) progress.current?.(); }, [content, streaming]);
  const { answer } = splitAssistantContent(content, streaming);
  const text = useMemo(() => stabilizeMarkdown(answer, { streaming }), [answer, streaming]);
  return <div className="min-w-0 space-y-4 break-words text-[15px] leading-7 [&>*:first-child]:mt-0" data-streaming={streaming ? "true" : undefined}>
    {text ? <StreamingContext value={streaming}><Markdown skipHtml urlTransform={safeLink} remarkPlugins={plugins} components={components}>{text}</Markdown></StreamingContext> : null}
  </div>;
});
