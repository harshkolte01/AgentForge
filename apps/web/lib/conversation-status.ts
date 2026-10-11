import type { ConversationSession } from "@/lib/workspace-api";

/** What the sidebar shows beside a conversation. `null` is nothing at all. */
export type SidebarStatus = "running" | "waiting_approval" | "failed" | "unread";

export const statusLabels: Record<SidebarStatus, string> = {
  running: "Working",
  waiting_approval: "Waiting for your approval",
  failed: "Failed",
  unread: "New reply",
};

type StatusFields = Pick<ConversationSession, "status" | "active_task_id">;

/**
 * The status to show for a conversation. Work in progress always shows, so a conversation you are looking at still tells
 * you it is waiting on you. A failure or a finished reply you have not seen is a message about something you have not
 * looked at, so it is not shown for the conversation you are looking at: that is the one place it has just been seen.
 * An older server sends no `status`; then only the active task is known.
 */
export function sidebarStatus(session: StatusFields, viewing: boolean): SidebarStatus | null {
  if (session.status === "waiting_approval") return "waiting_approval";
  if (session.status === "running" || session.active_task_id) return "running";
  if (viewing) return null;
  return session.status === "failed" || session.status === "unread" ? session.status : null;
}

/** Whether the server still counts this conversation's latest outcome as unseen, so opening it should say it was seen. */
export function hasUnseenOutcome(session: Pick<ConversationSession, "status">): boolean {
  return session.status === "failed" || session.status === "unread";
}

/** Work that is still going on, so the sidebar should keep checking quickly. */
export function isLive(session: StatusFields): boolean {
  const status = sidebarStatus(session, false);
  return status === "running" || status === "waiting_approval";
}

/**
 * A sentence for a screen reader when a conversation's status changes while you are elsewhere, or null when nothing worth
 * saying happened (no change, a conversation seen for the first time, or a change to nothing).
 */
export function statusAnnouncement(title: string, before: SidebarStatus | null | undefined, after: SidebarStatus | null): string | null {
  if (before === undefined || before === after || after === null || after === "running") return null;
  const name = title.length > 60 ? `${title.slice(0, 59)}…` : title;
  if (after === "waiting_approval") return `${name} is waiting for your approval.`;
  if (after === "failed") return `${name} failed.`;
  return before === "running" || before === "waiting_approval" ? `${name} finished.` : `${name} has a new reply.`;
}
