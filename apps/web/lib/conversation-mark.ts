import { sidebarStatus, statusLabels, type SidebarStatus } from "@/lib/conversation-status";
import type { ConversationDelivery, ConversationSession } from "@/lib/workspace-api";

/**
 * The one mark at the start of a conversation's row in the sidebar. Work that needs you or is going on comes first (it is what you
 * would otherwise miss); after that the row says where its pull request stands, in the colour language the Delivery panel uses:
 * purple merged, red blocked (conflicts, failing checks, changes requested), amber while checks run, green approved, faint for
 * draft and closed. A healthy open pull request is the resting state and stays quiet, so colour is kept for rows with something
 * to say. A conversation that has produced nothing for GitHub shows a faint dot, which keeps every title on the same line.
 */
export type MarkKey =
  | SidebarStatus
  | "pr_merged" | "pr_closed" | "pr_conflicts" | "pr_changes" | "pr_failing" | "pr_running" | "pr_draft" | "pr_approved" | "pr_open"
  | "branch" | "unpublished" | "idle";

export type Mark = { key: MarkKey; label: string; /** Worth saying in the row's tooltip (a state, not just "open"). */ notable: boolean };

const pullRequestLabels: Record<Extract<MarkKey, `pr_${string}`>, string> = {
  pr_merged: "Pull request merged", pr_closed: "Pull request closed", pr_conflicts: "Pull request has conflicts", pr_changes: "Changes requested",
  pr_failing: "Checks failing", pr_running: "Checks running", pr_draft: "Draft pull request", pr_approved: "Pull request approved", pr_open: "Pull request open",
};

export function pullRequestKey(delivery: ConversationDelivery | null | undefined): Extract<MarkKey, `pr_${string}`> | null {
  if (!delivery || delivery.state !== "pull_request" || !delivery.pr_state) return null;
  if (delivery.pr_state === "merged") return "pr_merged";
  if (delivery.pr_state === "closed") return "pr_closed";
  if (delivery.mergeable === "conflicting") return "pr_conflicts";
  if (delivery.review === "changes_requested") return "pr_changes";
  if (delivery.checks === "failing") return "pr_failing";
  if (delivery.checks === "pending") return "pr_running";
  if (delivery.pr_state === "draft") return "pr_draft";
  if (delivery.review === "approved") return "pr_approved";
  return "pr_open";
}

export function conversationMark(session: Pick<ConversationSession, "status" | "active_task_id" | "delivery">, viewing: boolean): Mark {
  const status = sidebarStatus(session, viewing);
  if (status) return { key: status, label: statusLabels[status], notable: true };
  const pr = pullRequestKey(session.delivery);
  if (pr) return { key: pr, label: pullRequestLabels[pr], notable: pr !== "pr_open" };
  const delivery = session.delivery;
  if (delivery?.unpublished) return { key: "unpublished", label: "Changes not yet published", notable: true };
  if (delivery?.state === "branch") return { key: "branch", label: "Branch published", notable: true };
  return { key: "idle", label: "", notable: false };
}
