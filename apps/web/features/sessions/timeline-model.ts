/**
 * The pure parts of the activity timeline: what to say when the connection struggles, what a screen reader is told, how a
 * finished (or abandoned) run is projected, and how entries are laid out in sections. Kept free of React so every rule here
 * has a plain node test.
 */
import { describeCall, isDetailable } from "@/lib/activity/presentation";
import type { ActivityEntry, CallEntry, PhaseEntry, ToolCallOutcome } from "@/lib/activity/types";
import type { SyncSnapshot } from "@/lib/activity/sync";
import { isToolKind, type LiveState, type LiveToolView } from "@/lib/activity/live-text";

// ---------------------------------------------------------------------------------------------------------------------
// Connection notices

/** Consecutive failed reads before "Reconnecting…" becomes "Couldn't load the latest activity" with a Retry button. */
export const RECONNECT_ATTEMPTS = 3;

export type TimelineNotice =
  /** A read failed or the stream dropped; the controller is retrying by itself. */
  | { kind: "reconnecting"; text: string }
  /** Missing events are being fetched. */
  | { kind: "catching_up"; text: string }
  /** Retries keep failing: say so, and offer a manual retry. */
  | { kind: "failed"; text: string }
  /** Nothing more will be tried (session ended, task gone). No retry button. */
  | { kind: "halted"; text: string };

type NoticeInput = Pick<SyncSnapshot, "phase" | "loading" | "attempts" | "error" | "halted" | "awaitingAnswer">;

/**
 * The line to show inside the timeline for the controller's state, or null when everything is fine. Compact and calm: a
 * first failure reads "Reconnecting…", not an error; only repeated failure asks the person to act.
 */
export function syncNotice(sync: NoticeInput): TimelineNotice | null {
  if (sync.halted) {
    return sync.error?.kind === "not_found"
      ? { kind: "halted", text: "This activity is no longer available" }
      : { kind: "halted", text: "Your session ended. Sign in again to see activity" };
  }
  const struggling = !!sync.error && (sync.phase === "retrying" || sync.phase === "unreachable");
  if (struggling) {
    if (sync.attempts >= RECONNECT_ATTEMPTS) return { kind: "failed", text: "Couldn't load the latest activity" };
    return sync.error?.kind === "incomplete" ? { kind: "catching_up", text: "Catching up…" } : { kind: "reconnecting", text: "Reconnecting…" };
  }
  // Reading events the view has not seen yet is how a live run is followed (every new step is read this way), not trouble:
  // naming it would swap "Thinking" for "Catching up…" and back several times a run. Only a failed read is worth a line.
  return null;
}

// ---------------------------------------------------------------------------------------------------------------------
// Announcements

const endedText: Record<string, string> = {
  COMPLETED: "Finished",
  PARTIAL: "Finished with partial results",
  CANCELLED: "Stopped",
  FAILED: "Couldn't finish",
};

export type AnnouncementInput = {
  notice: TimelineNotice | null;
  /** The task's terminal status once it is over. */
  ended: string | null;
  stopping: boolean;
  actions: number;
};

/**
 * The one short sentence a screen reader hears about the run. Streamed words are never announced: only state changes (working,
 * how many actions, stopping, finished, stopped) and connection trouble. The caller shows it in a polite `sr-only` status.
 */
export function activityAnnouncement({ notice, ended, stopping, actions }: AnnouncementInput): string {
  if (ended) return endedText[ended] ?? "Finished";
  if (notice && notice.kind !== "catching_up") return notice.kind === "reconnecting" ? "Reconnecting" : notice.text;
  if (stopping) return "Stopping";
  if (actions > 0) return `Working, ${actions === 1 ? "1 action" : `${actions} actions`}`;
  return "Working";
}

/** "41s", "2m 4s", "1h 3m". */
export function formatDuration(ms: number): string {
  const seconds = Math.max(1, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return minutes < 60 ? `${minutes}m ${seconds % 60}s` : `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** Milliseconds from the first to the last event of a run, or null when there are fewer than two readable timestamps. */
export function runDuration(events: readonly { created_at: string }[]): number | null {
  let first = Infinity;
  let last = -Infinity;
  for (const event of events) {
    const time = Date.parse(event.created_at);
    if (Number.isNaN(time)) continue;
    first = Math.min(first, time);
    last = Math.max(last, time);
  }
  return last > first ? last - first : null;
}

/** The label of the closed disclosure: "Worked · 41s · 22 steps". Each part is left out when unknown. */
export function activityLabel(actions: number, durationMs?: number | null): string {
  const parts = ["Worked"];
  if (durationMs != null && durationMs > 0) parts.push(formatDuration(durationMs));
  if (actions > 0) parts.push(actions === 1 ? "1 step" : `${actions} steps`);
  return parts.join(" · ");
}

// ---------------------------------------------------------------------------------------------------------------------
// Entries

/** Exact counts of what a list of entries holds: calls (inside groups too) and progress notes. */
export function summarizeEntries(entries: readonly ActivityEntry[]): { actions: number; notes: number } {
  let actions = 0;
  let notes = 0;
  for (const entry of entries) {
    if (entry.kind === "call") actions++;
    else if (entry.kind === "group") actions += entry.count;
    else if (entry.kind === "note") notes++;
  }
  return { actions, notes };
}

/** The newest running call: the one row that gets the orb and shimmer. Other running calls keep a calm dot. */
export function currentCallId(entries: readonly ActivityEntry[]): string | null {
  for (let i = entries.length - 1; i >= 0; i--) {
    const entry = entries[i];
    if (entry.kind === "call" && entry.outcome === "running") return entry.id;
  }
  return null;
}

export type Section =
  | { kind: "note"; id: string; entry: Extract<ActivityEntry, { kind: "note" }> }
  | { kind: "phase"; id: string; entry: PhaseEntry }
  /** Consecutive steps (calls, groups, milestones) share one list. */
  | { kind: "rows"; id: string; entries: Array<Exclude<ActivityEntry, { kind: "note" | "phase" }>> };

/** Notes and headings stand between the lists of steps that came before and after them. */
export function sectionsOf(entries: readonly ActivityEntry[]): Section[] {
  const out: Section[] = [];
  for (const entry of entries) {
    if (entry.kind === "note") out.push({ kind: "note", id: entry.key, entry });
    else if (entry.kind === "phase") out.push({ kind: "phase", id: entry.key, entry });
    else {
      const last = out[out.length - 1];
      if (last?.kind === "rows") last.entries.push(entry);
      else out.push({ kind: "rows", id: entry.key, entries: [entry] });
    }
  }
  return out;
}

// ---------------------------------------------------------------------------------------------------------------------
// Subagents

/** Any entry that belongs in a list of steps (notes and headings stand between the lists). */
export type RowEntry = Exclude<ActivityEntry, { kind: "note" | "phase" }>;

/** One item of a list of steps: an ordinary entry, or delegations that stand next to each other and are shown as cards. */
export type RowChunk =
  | { kind: "entry"; key: string; entry: RowEntry }
  | { kind: "subagents"; key: string; calls: readonly CallEntry[] };

const isDelegation = (entry: RowEntry): entry is CallEntry => entry.kind === "call" && entry.tool === "delegate_task";

/**
 * A list of steps as the chat lays it out. Delegations that follow one another (several requested in one reply, or one after
 * the other) form one chunk, so a team of subagents reads as a team; every other entry stays a row of its own. Order is kept.
 */
export function chunkRows(entries: readonly RowEntry[]): RowChunk[] {
  const out: RowChunk[] = [];
  for (const entry of entries) {
    const last = out[out.length - 1];
    if (!isDelegation(entry)) out.push({ kind: "entry", key: entry.key, entry });
    else if (last?.kind === "subagents") out[out.length - 1] = { kind: "subagents", key: last.key, calls: [...last.calls, entry] };
    else out.push({ kind: "subagents", key: entry.key, calls: [entry] });
  }
  return out;
}

/** The delegations among `entries`, by the run id of the subagent each started (calls from older runs have none). */
export function subagentsByRun(entries: readonly ActivityEntry[]): Map<string, CallEntry> {
  const out = new Map<string, CallEntry>();
  for (const entry of entries) if (entry.kind === "call" && entry.childRunId) out.set(entry.childRunId, entry);
  return out;
}

/** Who an Approve / Decline card is from: a subagent says which assignment it is working on; the main agent's own cards say nothing. */
export function approvalSource(agentRunId: string | undefined, subagents: ReadonlyMap<string, CallEntry>): string | undefined {
  if (!agentRunId) return undefined;
  const title = subagents.get(agentRunId)?.detail;
  return title ? `Subagent · ${title}` : "Subagent";
}

const subagentStatusText: Record<ToolCallOutcome, string> = {
  running: "Working", completed: "Finished", failed: "Couldn't finish", blocked: "Blocked", cancelled: "Stopped", unknown: "Interrupted",
};

/** What a subagent's card says it is doing, in one word or two. */
export const subagentStatus = (outcome: ToolCallOutcome): string => subagentStatusText[outcome];

/**
 * The heading over two or more subagents: how many, and how they stand. Never claims they ran at the same time (they may
 * have run one after another), only what is true now.
 */
export function fleetSummary(calls: readonly CallEntry[]): string {
  const working = calls.filter((call) => call.outcome === "running").length;
  const trouble = calls.filter((call) => call.outcome !== "running" && call.outcome !== "completed").length;
  const head = `${calls.length} subagents`;
  if (working) return `${head} · ${working} working`;
  if (!trouble) return `${head} · all finished`;
  return trouble === calls.length ? `${head} · none finished` : `${head} · ${calls.length - trouble} finished, ${trouble} didn't finish`;
}

/**
 * The line under a running subagent's title: the call it has open, else its latest note, else that it is starting. Null once
 * it is over, and for a run that did not report its subagent's progress (nothing is made up).
 */
export function subagentNow(call: CallEntry): string | null {
  if (call.outcome !== "running" || !call.child) return null;
  const active = call.child.active[call.child.active.length - 1];
  if (active) return active.detail ? `${active.label} · ${active.detail}` : active.label;
  const note = call.child.note?.split("\n").find((line) => line.trim());
  return note ? note.trim() : "Getting started";
}

/** "1 step", "12 steps"; null when the subagent has not started one or its progress is unknown. */
export function subagentSteps(call: CallEntry): string | null {
  const steps = call.child?.steps ?? 0;
  return steps > 0 ? (steps === 1 ? "1 step" : `${steps} steps`) : null;
}

const endedCalls = new WeakMap<CallEntry, CallEntry>();
const endedPhases = new WeakMap<PhaseEntry, PhaseEntry>();

/** A call that was running when the task ended, as far as the client can tell: the effect is not known, nothing is invented. */
function ended(call: CallEntry): CallEntry {
  let unknown = endedCalls.get(call);
  if (!unknown) {
    const shown = describeCall({ tool: call.tool, outcome: "unknown", rawDetail: call.rawDetail, exitCode: call.exitCode, timedOut: call.timedOut, qualified: call.qualified });
    const { detail, ...rest } = shown ?? { label: call.label, tone: call.tone, icon: call.icon };
    const base: CallEntry = { ...call };
    delete base.detail; // the running row's detail must not outlive its own wording
    unknown = { ...base, ...rest, ...(detail ? { detail } : {}), outcome: "unknown", detailable: isDetailable(call.presentation, call.toolCallId, "unknown") };
    endedCalls.set(call, unknown);
  }
  return unknown;
}

/**
 * The entries of a task that is over, with nothing left spinning. The reducer already does this once every event up to the
 * task's end has been read; this covers the interval where the task is known to be over but the last events could not be
 * fetched (a failing catch-up), so an unfinished call never shows an orb forever. Entries keep their identity when unchanged.
 */
export function endEntries(entries: readonly ActivityEntry[]): readonly ActivityEntry[] {
  if (!entries.some((entry) => (entry.kind === "call" && entry.outcome === "running") || (entry.kind === "phase" && entry.state === "running"))) return entries;
  return entries.map((entry) => {
    if (entry.kind === "call" && entry.outcome === "running") return ended(entry);
    if (entry.kind === "phase" && entry.state === "running") {
      let done = endedPhases.get(entry);
      if (!done) { done = { ...entry, state: "done" }; endedPhases.set(entry, done); }
      return done;
    }
    return entry;
  });
}

// ---------------------------------------------------------------------------------------------------------------------
// A tool call shown live

/** Where the live tool call shows: as a row of its own while it is written, under its running row while it prints. */
export type LiveToolPlacement =
  | { kind: "draft"; call: LiveToolView }
  | { kind: "output"; entryId: string; text: string };

/** The call with this id (the `call_id` its start event carries), wherever it sits, folded into a group or not. */
function findCall(entries: readonly ActivityEntry[], callId: string): CallEntry | undefined {
  for (const entry of entries) {
    if (entry.kind === "call" && entry.callId === callId) return entry;
    if (entry.kind === "group") { const call = entry.calls.find((item) => item.callId === callId); if (call) return call; }
  }
  return undefined;
}

/**
 * What to show of the live tool call. While the model writes a call it has no row yet, so it gets one of its own, until the
 * real row exists (the start event arrived: the real row takes over, so the call is never shown twice) or an Approve / Decline
 * card is waiting for it (the card carries the exact text). Once it runs, its output shows under its row, and only while that
 * row says it is running: a settled row has its own details.
 */
export function placeLiveTool(entries: readonly ActivityEntry[], live: LiveToolView | null, approvalsPending: boolean): LiveToolPlacement | null {
  if (!live) return null;
  const call = findCall(entries, live.callId);
  if (live.phase === "tool") return call || approvalsPending ? null : { kind: "draft", call: live };
  return call && call.outcome === "running" && live.text ? { kind: "output", entryId: call.id, text: live.text } : null;
}

// ---------------------------------------------------------------------------------------------------------------------
// Live text held through the end of a run

/** The last live text shown, remembered so the end of a run cannot blank it before the saved answer takes over. */
export type HeldLive = { epoch: number; kind: LiveState["kind"]; text: string };

/**
 * What to keep of the live text across updates. The server clears the live copy when a task ends, which arrives as an empty
 * reset: while the run is going that means "discarded" and the text must go, but once the task is known to be over it only
 * means "the saved answer is on its way", so the last text stays until `savedReady` hands over to the saved message (a note that
 * was saved meanwhile hides it through `visibleLive`). Identity is kept while nothing changed.
 */
export function holdLive(held: HeldLive | null, live: Pick<LiveState, "epoch" | "kind" | "text">, over: boolean): HeldLive | null {
  // A tool call is not words to keep on screen: it is shown by the timeline while it lasts and is gone when it settles.
  if (isToolKind(live.kind)) return over ? held : null;
  if (live.text) return held && held.epoch === live.epoch && held.kind === live.kind && held.text === live.text ? held : { epoch: live.epoch, kind: live.kind, text: live.text };
  return over ? held : null;
}

/** The held text as live state, for `visibleLive`. */
export function heldAsLive(held: HeldLive): LiveState {
  return { epoch: held.epoch, revision: 0, kind: held.kind, text: held.text, length: held.text.length };
}
