import { CircleAlert, ShieldQuestion } from "lucide-react";
import { statusLabels, type SidebarStatus } from "@/lib/conversation-status";

/**
 * The status mark beside a conversation in the sidebar. Each state differs in shape as well as colour: moving dots while it
 * works (the "ASCII braille" dot spinner), a shield with a question mark while it waits for you, an alert circle on failure and a plain dot for a new reply.
 */
export function SessionStatus({ status }: { status: SidebarStatus | null }) {
  if (!status) return null;
  const label = statusLabels[status];
  if (status === "running") return <span role="img" aria-label={label} data-status="running" className="status-braille shrink-0 text-foreground"><i /><i /><i /><i /><i /><i /><i /><i /></span>;
  if (status === "waiting_approval") return <ShieldQuestion role="img" aria-label={label} data-status="waiting_approval" className="size-3.5 shrink-0 text-amber-600 dark:text-amber-400" />;
  if (status === "failed") return <CircleAlert role="img" aria-label={label} data-status="failed" className="size-3.5 shrink-0 text-destructive" />;
  return <span role="img" aria-label={label} data-status="unread" className="status-unread size-2 shrink-0 rounded-full bg-sky-600 dark:bg-sky-400" />;
}
