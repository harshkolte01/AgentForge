/**
 * Approval of the agent's tool calls. In "Ask me" mode the run stops before a command, a file write or edit, or a pull request,
 * and the chat shows an Approve / Decline card. The card is not stored anywhere on its own: it is derived from the run's events
 * (`approval.required` with no `approval.decided` after it), so a reload, a second tab or a late reader sees the same cards.
 */
import type { TaskEvent } from "@/lib/task-api";

export type PendingApproval = {
  id: string;
  tool: string;
  /**
   * Exactly what will run, as the call will use it and unredacted (a rewritten command is not the command that runs): the
   * whole command; the path and full content of a write; the path with old and new text of an edit; a pull request's title,
   * description and changed files.
   */
  detail: string;
  /** The server had to cut `detail` short: what is shown is not all of it. False when absent (older servers never cut it). */
  truncated: boolean;
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
        truncated: event.payload.truncated === true,
      });
    } else if (event.event_type === "approval.decided") {
      waiting.delete(id);
    }
  }
  return [...waiting.values()].sort((a, b) => a.sequence - b.sequence);
}

/** The size of a detail, said when it is too long to read at a glance: "42 lines · 3,120 characters". Null when it is short. */
export function detailSize(detail: string, { lines: maxLines = 8, characters: maxCharacters = 600 } = {}): string | null {
  const lines = detail ? detail.split("\n").length : 0;
  if (lines <= maxLines && detail.length <= maxCharacters) return null;
  const count = (value: number, one: string, many: string) => `${value.toLocaleString("en-US")} ${value === 1 ? one : many}`;
  return `${count(lines, "line", "lines")} · ${count(detail.length, "character", "characters")}`;
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
