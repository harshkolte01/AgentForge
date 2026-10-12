import type { ConversationSession } from "@/lib/workspace-api";

/** A quiet label for the open conversation, sourced from its saved repository. */
export function ConversationIdentity({ session }: { session: Pick<ConversationSession, "title" | "repository"> }) {
  const repository = session.repository?.repo_id.trim();
  const name = repository?.split("/").filter(Boolean).at(-1);
  const title = session.title.trim() || "Untitled conversation";
  return <h1 data-conversation-identity className="flex min-w-0 max-w-xl flex-1 items-center gap-1 text-[13px] leading-5 font-normal">
    <span className="max-w-[40%] shrink-0 truncate text-muted-foreground" title={name ? repository : "Chat"}>
      {name ? <span className="sr-only">Repository: </span> : null}{name || "Chat"}
    </span>
    <svg className="h-3.5 w-1.5 shrink-0 text-muted-foreground/60" viewBox="0 0 6 14" fill="none" aria-hidden="true" focusable="false">
      <path d="M4.5 2 1.5 12" stroke="currentColor" strokeWidth="1" strokeLinecap="round" />
    </svg>
    <span className="min-w-0 truncate text-foreground/80" title={title}>
      <span className="sr-only">Conversation: </span>{title}
    </span>
  </h1>;
}
