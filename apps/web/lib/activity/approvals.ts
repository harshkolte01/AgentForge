/**
 * Approval of the agent's tool calls. In "Ask me" mode the run stops before a command, a file write or edit, or a pull request,
 * and the chat shows an Approve / Decline card. The card is not stored anywhere on its own: it is derived from the run's events
 * (`approval.required` with no `approval.decided` after it), so a reload, a second tab or a late reader sees the same cards.
 */
import type { TaskEvent } from "@/lib/task-api";

export type PendingApproval = {
  id: string;
  tool: string;
  /** The command, path or title exactly as the call will use it (credentials already removed by the server). */
  detail: string;
  sequence: number;
};

/** The calls waiting for an answer, oldest first. Pure: safe in render. */
export function pendingApprovals(events: readonly TaskEvent[]): PendingApproval[] {
  const waiting = new Map<string, PendingApproval>();
  for (const event of events) {
    const id = typeof event.payload.approval_id === "string" ? event.payload.approval_id : null;
    if (!id) continue;
    if (event.event_type === "approval.required") {
      waiting.set(id, {
        id, sequence: event.sequence, tool: typeof event.payload.tool === "string" ? event.payload.tool : "",
        detail: typeof event.payload.detail === "string" ? event.payload.detail : "",
      });
    } else if (event.event_type === "approval.decided") {
      waiting.delete(id);
    }
  }
  return [...waiting.values()].sort((a, b) => a.sequence - b.sequence);
}

/** What the card says it is asking, by tool. */
export function approvalTitle(tool: string): string {
  switch (tool) {
    case "bash": return "Run this command?";
    case "write": return "Write this file?";
    case "edit": return "Edit this file?";
    case "create_pull_request": return "Open this pull request?";
    default: return "Allow this action?";
  }
}

export type ApprovalMode = "ask" | "auto";
