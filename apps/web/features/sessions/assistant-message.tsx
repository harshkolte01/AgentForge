import Markdown from "react-markdown";

function safeLink(value: string) {
  if (value.startsWith("#") || (value.startsWith("/") && !value.startsWith("//") && !value.includes("\\"))) return value;
  try { const url = new URL(value); return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : ""; }
  catch { return ""; }
}

export function AssistantMessage({ content }: { content: string }) {
  return <div className="min-w-0 space-y-3 break-words text-sm leading-6"><Markdown skipHtml urlTransform={safeLink} components={{
    p: ({ children }) => <p className="whitespace-pre-line">{children}</p>,
    h1: ({ children }) => <h2 className="pt-2 text-sm font-semibold">{children}</h2>,
    h2: ({ children }) => <h3 className="pt-2 text-sm font-semibold">{children}</h3>,
    h3: ({ children }) => <h4 className="pt-2 text-sm font-semibold">{children}</h4>,
    ul: ({ children }) => <ul className="list-disc space-y-1 pl-5">{children}</ul>,
    ol: ({ children }) => <ol className="list-decimal space-y-1 pl-5">{children}</ol>,
    pre: ({ children }) => <pre className="max-w-full overflow-x-auto rounded-lg bg-muted p-3 text-xs leading-5 [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-inherit" tabIndex={0} aria-label="Code block">{children}</pre>,
    code: ({ children }) => <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em]">{children}</code>,
    blockquote: ({ children }) => <blockquote className="border-l pl-4 text-muted-foreground">{children}</blockquote>,
    img: ({ alt }) => alt ? <span>{alt}</span> : null,
    a: ({ href, children }) => href ? <a href={href} className="rounded underline underline-offset-4 hover:text-muted-foreground focus-visible:outline-2" {...(href.startsWith("http") ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{children}</a> : <span>{children}</span>,
  }}>{content}</Markdown></div>;
}
