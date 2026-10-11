/**
 * Types shared by the activity modules: wire shapes (re-exported from `task-api`, which mirrors the backend contracts) and
 * the view model the reducer builds from them. Pure types, so importing this file costs nothing at runtime.
 */
import type {
  ActivitySummary,
  LiveStreamSnapshot,
  LiveTextKind,
  MessageActivity,
  TaskEvent,
  ToolCallDetail,
  ToolCallOutcome,
} from "@/lib/task-api";

export type { ActivitySummary, LiveStreamSnapshot, LiveTextKind, MessageActivity, TaskEvent, ToolCallDetail, ToolCallOutcome };

/** `LiveStreamSnapshot` is also what the client keeps as its live state: see `live-text.ts`. */
export type LiveSnapshot = LiveStreamSnapshot;

/** One SSE `message.delta` frame. Offsets count Unicode code points (the server's `len(text)`), not UTF-16 units. */
export type LiveDelta = {
  task_id: string;
  epoch: number;
  revision: number;
  kind: LiveTextKind;
  offset: number;
  text: string;
  /** Replace the whole live text with `text` (offset is 0). An empty reset means "this text was cleared". */
  reset: boolean;
  /** The call a `tool` or `output` frame is about (fixed for an epoch and revision); absent on prose. */
  tool?: string | null;
  call_id?: string | null;
  detail?: string | null;
};

/** The tool call a live row shows: written by the model (`tool`) or running (`output`). */
export type LiveTool = {
  name: string;
  /** The id the call's tool.started event carries (`CallEntry.callId`). */
  callId: string;
  /** What it is about (a path, a title), when the server said. */
  detail: string | null;
};

/** How a presented tool is shown. `status` rows are compact lines; the others can be expanded for details. */
export type PresentationKind = "status" | "execution" | "diff" | "artifact" | "subagent" | "hidden";

/** The meaning of a row's colour. Always paired with text and an `icon`, so colour is never the only signal. */
export type Tone = "success" | "danger" | "warning" | "neutral";

/** Which glyph a row should show. */
export type StatusIcon = "running" | "success" | "failed" | "warning" | "stopped" | "blocked" | "unknown";

/** An inclusive range of event sequences. */
export type SequenceRange = readonly [from: number, to: number];

/** A progress note the agent wrote for the person: durable (`assistant.message`). */
export type NoteEntry = {
  kind: "note";
  id: string;
  /** React key; equal to `id`. */
  key: string;
  sequence: number;
  text: string;
  /** The live-text epoch this note was written from, when the server said so. */
  epoch: number | null;
};

/** A heading that names a change of phase ("Checking the result"). Never stands for work in progress. */
export type PhaseEntry = {
  kind: "phase";
  id: string;
  key: string;
  sequence: number;
  label: string;
  state: "running" | "done" | "failed";
};

/** A one-off line that is not a tool call ("Prepared the workspace", "Fixing failing tests"). */
export type MilestoneEntry = {
  kind: "milestone";
  id: string;
  key: string;
  sequence: number;
  label: string;
};

/** What a subagent is doing and has done, read from its own events so its card can show it without opening the report. */
export type SubagentProgress = {
  /** Tool calls it has started. */
  steps: number;
  /** Its calls that have not finished, oldest first. The newest is what it is doing now. */
  active: readonly { id: string; label: string; detail?: string }[];
  /** The last note it wrote for the person, cut short. Null until it writes one. */
  note: string | null;
};

/** One tool call: running, or settled with an outcome. */
export type CallEntry = {
  kind: "call";
  /** `call:<tool_call_id>`, or `call:<sequence>` for a legacy call that has no persisted id. */
  id: string;
  /** React key: the first call's `id`, which stays the same when this call is later folded into a group. */
  key: string;
  /** Sequence of the event that created the row (the start, or the finish when the start was never seen). */
  sequence: number;
  tool: string;
  toolCallId: string | null;
  /** The gateway operation-key suffix older events carry. Kept for legacy matching only. */
  callId: string | null;
  agentRunId: string | null;
  presentation: Exclude<PresentationKind, "hidden">;
  outcome: ToolCallOutcome;
  label: string;
  /** What the call was about (query, host, path), formatted for display. */
  detail?: string;
  /** The detail exactly as the server sent it. */
  rawDetail: string | null;
  tone: Tone;
  icon: StatusIcon;
  startSequence: number | null;
  finishSequence: number | null;
  exitCode: number | null;
  timedOut: boolean | null;
  qualified: boolean | null;
  errorCode: string | null;
  durationMs: number | null;
  evidenceIds: readonly string[];
  artifactIds: readonly string[];
  /** The details panel can be requested (`fetchToolDetail`): an execution, diff or artifact call that has a persisted id and has settled. */
  detailable: boolean;
  /** A delegation only: the run of the subagent it started. Its events carry this as their `agent_run_id`. */
  childRunId?: string;
  /** A delegation only: the subagent's progress so far. Absent for older runs that did not name their child. */
  child?: SubagentProgress;
  /** When the start event was recorded (milliseconds since the epoch), for a running clock. */
  startedAt?: number;
  /** A legacy finish that could belong to several running calls; it got its own row instead of guessing. */
  ambiguous: boolean;
};

/** Consecutive finished calls of one tool, folded into a counted line. The individual calls stay inspectable. */
export type GroupEntry = {
  kind: "group";
  /** `group:<first call id>`. */
  id: string;
  /** React key: the first call's `id`. */
  key: string;
  sequence: number;
  tool: string;
  count: number;
  label: string;
  tone: Tone;
  icon: StatusIcon;
  outcome: "completed";
  calls: readonly CallEntry[];
};

export type ActivityEntry = NoteEntry | PhaseEntry | MilestoneEntry | CallEntry | GroupEntry;

/** How the task ended, as far as the client knows. */
export type TerminalInfo = {
  status: string;
  /** The task's final event sequence (`task.finished`, or the watermark when the task was read as terminal); null when unknown. */
  sequence: number | null;
};

export type ActivityOptions = {
  /**
   * What to do with a tool name the registry does not list: `generic` shows it with a safe generic label and counts it
   * (the backend only emits events for registered tools); `hide` drops it (the legacy `buildActivity` behaviour).
   */
  unlistedTools?: "generic" | "hide";
};

/** The reducer's state. Treat as immutable: every change returns a new object and keeps untouched entries by identity. */
export type ActivityState = {
  readonly taskId: string;
  readonly unlistedTools: "generic" | "hide";
  /** Observed event sequences, as sorted, merged inclusive ranges (every event type, not only activity ones). */
  readonly ranges: readonly SequenceRange[];
  /** The largest sequence observed. */
  readonly highest: number;
  /** The largest N such that sequences 1..N have all been observed. */
  readonly contiguous: number;
  /** The activity events seen, ordered by sequence. */
  readonly events: readonly TaskEvent[];
  /** Entries as the events say, before terminal reconciliation. */
  readonly entries: readonly ActivityEntry[];
  /** Entries to show: `entries`, with calls that never finished shown as `unknown` once the task is over. */
  readonly view: readonly ActivityEntry[];
  readonly terminal: TerminalInfo | null;
  /** Calls that have started and not finished, oldest first (bookkeeping for matching a finish to its start). */
  readonly running: readonly CallEntry[];
  /** Calls the registry counts as actions (hidden tools never count). */
  readonly actionCount: number;
  /** Progress notes. */
  readonly noteCount: number;
  /** The newest live-text epoch whose words were saved as a note. */
  readonly committedEpoch: number;
};

export type ActivityAction =
  /** Events in any order, with duplicates and events of other tasks tolerated. */
  | { type: "events"; events: readonly TaskEvent[] }
  /** The task is over (from the task row, the activity summary or a `task.finished` event). */
  | { type: "terminal"; status: string; sequence?: number | null };
