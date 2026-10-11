/**
 * The task-scoped activity reducer: one model for a live turn and for a finished one. It takes the task's durable events
 * (from the stream, from history pages, in any order, with duplicates) and keeps
 *
 *  - the observed sequences, so "what I have in order" (`contiguous`) is never confused with "the largest I have seen"
 *    (`highest`) and a hole is reported as a missing range instead of being skipped;
 *  - the timeline entries, ordered by sequence with stable ids. Appending an event copies pointers only: entries the event
 *    did not touch keep their object identity (so `React.memo` can skip them), including after an out-of-order event
 *    forces a rebuild;
 *  - the terminal state, so a call that never reported back renders as `unknown` once the task is over instead of
 *    spinning forever.
 *
 * Pure and framework-free: safe in `useReducer` (including StrictMode's double invocation), in a worker and in tests.
 * Start/finish pairs match by the persisted tool-call id. Legacy events (no id) match only when exactly one running call
 * could be the one; otherwise the finish gets its own row, flagged `ambiguous`. Nothing is guessed.
 */
import {
  canFold, describeCall, groupLabel, isActionTool, isDetailable, outcomeOf, phaseLabel, toolPresentation,
} from "@/lib/activity/presentation";
import type {
  ActivityAction, ActivityEntry, ActivityOptions, ActivityState, CallEntry, GroupEntry, MilestoneEntry, NoteEntry,
  PhaseEntry, SequenceRange, TaskEvent,
} from "@/lib/activity/types";

/** The kinds of run events that describe something the agent did or said. Everything else is bookkeeping. */
export const activityEventTypes: ReadonlySet<string> = new Set([
  "tool.started", "tool.finished", "subagent.started", "subagent.finished", "sandbox.workspace.created", "task.repair.started", "assistant.message",
  // Kept so the chat can show an Approve / Decline card (see `approvals.ts`); they add no row of their own.
  "approval.required", "approval.decided",
]);

export function createActivityState(taskId: string, options: ActivityOptions = {}): ActivityState {
  return {
    taskId, unlistedTools: options.unlistedTools ?? "generic", ranges: [], highest: 0, contiguous: 0, events: [], entries: [], view: [],
    terminal: null, running: [], actionCount: 0, noteCount: 0, committedEpoch: 0,
  };
}

// ---------------------------------------------------------------------------------------------------------------------
// Sequences

function hasSequence(ranges: readonly SequenceRange[], sequence: number): boolean {
  let low = 0;
  let high = ranges.length - 1;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (sequence < ranges[mid][0]) high = mid - 1;
    else if (sequence > ranges[mid][1]) low = mid + 1;
    else return true;
  }
  return false;
}

/** Adds ascending, not yet observed sequences to the ranges. Untouched ranges keep their identity. */
function addSequences(ranges: readonly SequenceRange[], sequences: readonly number[]): readonly SequenceRange[] {
  const out: SequenceRange[] = ranges.slice();
  let i = 0;
  for (const sequence of sequences) {
    while (i < out.length && out[i][1] < sequence - 1) i++;
    if (i < out.length && sequence >= out[i][0] - 1) {
      out[i] = [Math.min(out[i][0], sequence), Math.max(out[i][1], sequence)];
      if (i + 1 < out.length && out[i][1] + 1 >= out[i + 1][0]) { out[i] = [out[i][0], Math.max(out[i][1], out[i + 1][1])]; out.splice(i + 1, 1); }
    } else {
      out.splice(i, 0, [sequence, sequence]);
    }
  }
  return out;
}

const contiguousOf = (ranges: readonly SequenceRange[]) => ranges.length && ranges[0][0] === 1 ? ranges[0][1] : 0;

// ---------------------------------------------------------------------------------------------------------------------
// Building entries from events

type Draft = {
  entries: ActivityEntry[];
  running: CallEntry[];
  actionCount: number;
  noteCount: number;
  committedEpoch: number;
};

const text = (value: unknown): string | undefined => typeof value === "string" ? value : undefined;
const int = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
const flag = (value: unknown): boolean | null => typeof value === "boolean" ? value : null;
const strings = (value: unknown): readonly string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];

/** Index of the last entry with this id, searching from the end where recent entries live. */
function lastIndexById(entries: readonly ActivityEntry[], id: string): number {
  for (let i = entries.length - 1; i >= 0; i--) if (entries[i].id === id) return i;
  return -1;
}

function present(call: Omit<CallEntry, "label" | "detail" | "tone" | "icon" | "detailable">, policy: "generic" | "hide"): CallEntry {
  const shown = describeCall({
    tool: call.tool, outcome: call.outcome, rawDetail: call.rawDetail, exitCode: call.exitCode, timedOut: call.timedOut, qualified: call.qualified,
  }, policy);
  const detailable = isDetailable(call.presentation, call.toolCallId, call.outcome);
  // Hidden tools never reach here; the fallback keeps the type honest without inventing wording.
  if (!shown) return { ...call, label: call.tool, tone: "neutral", icon: "unknown", detailable };
  const { detail, ...rest } = shown;
  const base: Partial<CallEntry> & typeof call = { ...call };
  delete base.detail; // a call spread from an earlier state must not keep that state's detail
  return { ...base, ...rest, ...(detail ? { detail } : {}), detailable };
}

function startCall(draft: Draft, event: TaskEvent, policy: "generic" | "hide") {
  const { payload, sequence } = event;
  const tool = text(payload.tool);
  if (!tool || !isActionTool(tool, policy)) return;
  const toolCallId = text(payload.tool_call_id) ?? null;
  const id = toolCallId ? `call:${toolCallId}` : `call:${sequence}`;
  if (toolCallId && lastIndexById(draft.entries, id) >= 0) return; // a replayed start
  const presentation = toolPresentation(tool, policy) as CallEntry["presentation"];
  const call = present({
    kind: "call", id, key: id, sequence, tool, toolCallId, callId: text(payload.call_id) ?? null, agentRunId: text(payload.agent_run_id) ?? null,
    presentation, outcome: "running", rawDetail: text(payload.detail) ?? null, startSequence: sequence, finishSequence: null,
    exitCode: null, timedOut: null, qualified: null, errorCode: null, durationMs: null, evidenceIds: [], artifactIds: [], ambiguous: false,
  }, policy);
  draft.entries.push(call);
  draft.running.push(call);
  draft.actionCount++;
}

function settled(call: CallEntry, event: TaskEvent, policy: "generic" | "hide", ambiguous = false): CallEntry {
  const { payload, sequence } = event;
  return present({
    ...call,
    outcome: outcomeOf(payload), finishSequence: sequence,
    exitCode: int(payload.exit_code), timedOut: flag(payload.timed_out), qualified: flag(payload.qualified),
    errorCode: text(payload.error_code) ?? null, durationMs: int(payload.duration_ms),
    evidenceIds: strings(payload.evidence_ids), artifactIds: strings(payload.artifact_ids),
    // A finish that arrives carrying ids the start lacked (never expected, never invented) still names its call.
    agentRunId: call.agentRunId ?? text(payload.agent_run_id) ?? null,
    ambiguous,
  }, policy);
}

/** Finished calls of one tool, one after another, that say nothing more than their tool become one counted line. */
function makeGroup(calls: readonly CallEntry[]): GroupEntry {
  const first = calls[0];
  return {
    kind: "group", id: `group:${first.id.slice("call:".length)}`, key: first.key, sequence: first.sequence, tool: first.tool, count: calls.length,
    label: groupLabel(first.tool, calls.length) ?? first.label, tone: "success", icon: "success", outcome: "completed", calls,
  };
}

const foldable = (entry: ActivityEntry | undefined, tool: string): entry is CallEntry | GroupEntry =>
  !!entry && entry.kind !== "note" && entry.kind !== "phase" && entry.kind !== "milestone" && entry.tool === tool && (entry.kind === "group" || canFold(entry));

/** Folds the settled call at `index` with its neighbours when they are the same tool and also fold. */
function fold(entries: ActivityEntry[], index: number) {
  const call = entries[index];
  if (call.kind !== "call" || !canFold(call)) return;
  let from = index;
  let to = index;
  let calls: CallEntry[] = [call];
  const before = entries[index - 1];
  if (foldable(before, call.tool)) { calls = [...(before.kind === "group" ? before.calls : [before]), ...calls]; from = index - 1; }
  const after = entries[index + 1];
  if (foldable(after, call.tool)) { calls = [...calls, ...(after.kind === "group" ? after.calls : [after])]; to = index + 1; }
  if (calls.length > 1) entries.splice(from, to - from + 1, makeGroup(calls));
}

function hasCall(entries: readonly ActivityEntry[], toolCallId: string): boolean {
  return entries.some((entry) => entry.kind === "call" ? entry.toolCallId === toolCallId : entry.kind === "group" && entry.calls.some((call) => call.toolCallId === toolCallId));
}

function finishCall(draft: Draft, event: TaskEvent, policy: "generic" | "hide") {
  const { payload, sequence } = event;
  const tool = text(payload.tool);
  if (!tool || !isActionTool(tool, policy)) return;
  const toolCallId = text(payload.tool_call_id) ?? null;
  let open: CallEntry | undefined;
  let ambiguous = false;
  if (toolCallId) {
    open = draft.running.find((call) => call.toolCallId === toolCallId);
    // Already settled (a replayed or doubled finish): the first outcome stands. Only searched for when nothing is running
    // under this id, which keeps the usual finish free of any scan.
    if (!open && hasCall(draft.entries, toolCallId)) return;
  } else {
    // Legacy: the start has no persisted id, so only an unambiguous candidate counts. Several parallel calls of one tool
    // without ids cannot be told apart; the finish keeps its own row and the starts stay unsettled.
    const callId = text(payload.call_id);
    const candidates = draft.running.filter((call) => call.tool === tool && call.toolCallId === null && (!callId || call.callId === callId));
    if (candidates.length === 1) open = candidates[0];
    else ambiguous = candidates.length > 1;
  }
  if (open) {
    const index = lastIndexById(draft.entries, open.id);
    if (index < 0) return;
    draft.entries[index] = settled(open, event, policy);
    draft.running.splice(draft.running.indexOf(open), 1);
    fold(draft.entries, index);
    return;
  }
  // A finish whose start was never seen (a gap, or a legacy stream): it still happened, so it still shows.
  const id = toolCallId ? `call:${toolCallId}` : `call:${sequence}`;
  const presentation = toolPresentation(tool, policy) as CallEntry["presentation"];
  const orphan = settled({
    kind: "call", id, key: id, sequence, tool, toolCallId, callId: text(payload.call_id) ?? null, agentRunId: null, presentation, outcome: "running",
    label: "", rawDetail: null, tone: "neutral", icon: "running", startSequence: null, finishSequence: null, exitCode: null, timedOut: null,
    qualified: null, errorCode: null, durationMs: null, evidenceIds: [], artifactIds: [], detailable: false, ambiguous: false,
  }, event, policy, ambiguous);
  draft.entries.push(orphan);
  draft.actionCount++;
  fold(draft.entries, draft.entries.length - 1);
}

function applyEvent(draft: Draft, event: TaskEvent, policy: "generic" | "hide") {
  const { event_type: type, payload, sequence } = event;
  if (type === "assistant.message") {
    const epoch = int(payload.epoch);
    if (epoch !== null && epoch > draft.committedEpoch) draft.committedEpoch = epoch;
    const body = text(payload.text)?.trim();
    if (!body) return;
    const note: NoteEntry = { kind: "note", id: `note:${sequence}`, key: `note:${sequence}`, sequence, text: body, epoch };
    draft.entries.push(note);
    draft.noteCount++;
  } else if (type === "tool.started") {
    startCall(draft, event, policy);
  } else if (type === "tool.finished") {
    finishCall(draft, event, policy);
  } else if (type === "subagent.started") {
    const kind = text(payload.type);
    const label = kind ? phaseLabel(kind) : null;
    if (!label) return;
    // Two stages of one kind in a row (the stages of one research) share one heading.
    const previous = draft.entries.findLast((entry): entry is PhaseEntry => entry.kind === "phase");
    if (previous?.label === label) return;
    const id = `phase:${text(payload.spawn_id) ?? sequence}`;
    draft.entries.push({ kind: "phase", id, key: id, sequence, label, state: "running" });
  } else if (type === "subagent.finished") {
    const index = lastIndexById(draft.entries, `phase:${text(payload.spawn_id) ?? ""}`);
    const phase = draft.entries[index];
    if (phase?.kind === "phase") draft.entries[index] = { ...phase, state: payload.status === "FAILED" || payload.status === "BLOCKED" ? "failed" : "done" };
  } else if (type === "sandbox.workspace.created") {
    const milestone: MilestoneEntry = { kind: "milestone", id: `milestone:workspace:${sequence}`, key: `milestone:workspace:${sequence}`, sequence, label: "Prepared the workspace" };
    draft.entries.push(milestone);
  } else if (type === "task.repair.started") {
    const milestone: MilestoneEntry = { kind: "milestone", id: `milestone:repair:${sequence}`, key: `milestone:repair:${sequence}`, sequence, label: "Fixing failing tests" };
    draft.entries.push(milestone);
  }
}

const emptyDraft = (): Draft => ({ entries: [], running: [], actionCount: 0, noteCount: 0, committedEpoch: 0 });
const draftOf = (state: ActivityState): Draft => ({
  entries: state.entries.slice(), running: state.running.slice(), actionCount: state.actionCount, noteCount: state.noteCount, committedEpoch: state.committedEpoch,
});

// ---------------------------------------------------------------------------------------------------------------------
// Identity: reuse the previous object for every entry that came out the same

function same(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (typeof a !== "object" || typeof b !== "object" || !a || !b) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const left = a as Record<string, unknown>;
  const right = b as Record<string, unknown>;
  const keys = Object.keys(left);
  if (keys.length !== Object.keys(right).length) return false;
  return keys.every((key) => key in right && same(left[key], right[key]));
}

function reuseIdentity(previous: readonly ActivityEntry[], draft: Draft): void {
  if (!previous.length) return;
  const byId = new Map<string, ActivityEntry>();
  for (const entry of previous) {
    byId.set(entry.id, entry);
    if (entry.kind === "group") for (const call of entry.calls) byId.set(call.id, call);
  }
  const keep = <T extends ActivityEntry>(entry: T): T => {
    const old = byId.get(entry.id);
    return old && old.kind === entry.kind && same(old, entry) ? old as T : entry;
  };
  draft.entries = draft.entries.map((entry) => {
    if (entry.kind !== "group") return keep(entry);
    const calls = entry.calls.map(keep);
    const old = byId.get(entry.id);
    const candidate = calls.every((call, index) => call === entry.calls[index]) ? entry : { ...entry, calls };
    const unchanged = old?.kind === "group" && old.calls.length === calls.length && old.calls.every((call, index) => call === calls[index])
      && same({ ...old, calls: [] }, { ...candidate, calls: [] });
    return unchanged ? old : candidate;
  });
  // `running` must hold the very objects `entries` holds.
  draft.running = draft.running.map(keep);
}

// ---------------------------------------------------------------------------------------------------------------------
// The view: terminal reconciliation

/** The task is over and every event up to its end has been seen, so nothing more can report back. */
export function isSettled(state: Pick<ActivityState, "terminal" | "contiguous">): boolean {
  return !!state.terminal && (state.terminal.sequence === null || state.contiguous >= state.terminal.sequence);
}

const unknownCalls = new WeakMap<CallEntry, CallEntry>();
const donePhases = new WeakMap<PhaseEntry, PhaseEntry>();

/** A call that never reported back, once the task is over: its effect is not known, and nothing is invented. */
function asUnknown(call: CallEntry, policy: "generic" | "hide"): CallEntry {
  let unknown = unknownCalls.get(call);
  if (!unknown) {
    unknown = present({ ...call, outcome: "unknown" }, policy);
    unknownCalls.set(call, unknown);
  }
  return unknown;
}

function project(state: Pick<ActivityState, "entries" | "terminal" | "contiguous" | "unlistedTools">): readonly ActivityEntry[] {
  if (!isSettled(state)) return state.entries;
  return state.entries.map((entry) => {
    if (entry.kind === "call" && entry.outcome === "running") return asUnknown(entry, state.unlistedTools);
    if (entry.kind === "phase" && entry.state === "running") {
      let done = donePhases.get(entry);
      if (!done) { done = { ...entry, state: "done" }; donePhases.set(entry, done); }
      return done;
    }
    return entry;
  });
}

// ---------------------------------------------------------------------------------------------------------------------
// Reducer

function mergeSorted(left: readonly TaskEvent[], right: readonly TaskEvent[]): TaskEvent[] {
  const out: TaskEvent[] = [];
  let i = 0;
  let j = 0;
  while (i < left.length && j < right.length) out.push(left[i].sequence <= right[j].sequence ? left[i++] : right[j++]);
  while (i < left.length) out.push(left[i++]);
  while (j < right.length) out.push(right[j++]);
  return out;
}

function withView(state: Omit<ActivityState, "view">, previous: ActivityState): ActivityState {
  const view = state.entries === previous.entries && state.terminal === previous.terminal && state.contiguous === previous.contiguous ? previous.view : project(state);
  return { ...state, view };
}

function ingest(state: ActivityState, incoming: readonly TaskEvent[]): ActivityState {
  const fresh: TaskEvent[] = [];
  const seen = new Set<number>();
  for (const event of incoming) {
    if (event.task_id !== state.taskId || !Number.isInteger(event.sequence) || event.sequence < 1) continue;
    if (seen.has(event.sequence) || hasSequence(state.ranges, event.sequence)) continue;
    seen.add(event.sequence);
    fresh.push(event);
  }
  if (!fresh.length) return state;
  fresh.sort((a, b) => a.sequence - b.sequence);
  const ranges = addSequences(state.ranges, fresh.map((event) => event.sequence));
  const finished = fresh.findLast((event) => event.event_type === "task.finished");
  const terminal = finished
    ? { status: text(finished.payload.status) ?? state.terminal?.status ?? "FINISHED", sequence: finished.sequence }
    : state.terminal;
  const base = { ...state, ranges, highest: Math.max(state.highest, fresh[fresh.length - 1].sequence), contiguous: contiguousOf(ranges), terminal };
  const relevant = fresh.filter((event) => activityEventTypes.has(event.event_type));
  if (!relevant.length) return withView(base, state);

  const lastStored = state.events.length ? state.events[state.events.length - 1].sequence : 0;
  if (relevant[0].sequence > lastStored) {
    // The common case: newer events than anything seen. Existing entries are carried over untouched.
    const draft = draftOf(state);
    for (const event of relevant) applyEvent(draft, event, state.unlistedTools);
    return withView({ ...base, ...draft, events: state.events.concat(relevant) }, state);
  }
  // An event older than one already applied (a gap just filled): replay in order so start/finish pairs and folds come out right.
  const events = mergeSorted(state.events, relevant);
  const draft = emptyDraft();
  for (const event of events) applyEvent(draft, event, state.unlistedTools);
  reuseIdentity(state.entries, draft);
  return withView({ ...base, ...draft, events }, state);
}

export function reduceActivity(state: ActivityState, action: ActivityAction): ActivityState {
  if (action.type === "events") return ingest(state, action.events);
  const known = state.terminal?.sequence ?? null;
  const incoming = action.sequence ?? null;
  const sequence = known !== null && incoming !== null ? Math.max(known, incoming) : incoming ?? known;
  if (state.terminal?.status === action.status && state.terminal.sequence === sequence) return state;
  return withView({ ...state, terminal: { status: action.status, sequence } }, state);
}

// ---------------------------------------------------------------------------------------------------------------------
// Selectors

/** The entries to show, in order. Stable by identity when nothing about an entry changed. */
export const selectEntries = (state: ActivityState): readonly ActivityEntry[] => state.view;
/** Exact number of actions (calls the presentation registry counts, inside groups too). Not capped. */
export const selectActionCount = (state: ActivityState): number => state.actionCount;
/** Progress notes the agent wrote. */
export const selectNoteCount = (state: ActivityState): number => state.noteCount;
/** Whether there is anything to disclose: actions, or notes alone. */
export const selectHasActivity = (state: ActivityState): boolean => state.actionCount > 0 || state.noteCount > 0;
/** Whether a call is running right now (never true once the task is over and fully read). Headings and notes are never "now". */
export const selectHasRunning = (state: ActivityState): boolean => state.running.length > 0 && !isSettled(state);
/** The id of the newest running call: the one that gets the orb and shimmer; other running calls show a calm marker. */
export const selectCurrentCallId = (state: ActivityState): string | null => selectHasRunning(state) ? state.running[state.running.length - 1].id : null;
/** The newest live-text epoch whose words were saved as a note: live text of that epoch or older is already in the timeline. */
export const selectCommittedEpoch = (state: ActivityState): number => state.committedEpoch;
/** The largest N such that events 1..N have all been observed. */
export const selectContiguous = (state: ActivityState): number => state.contiguous;
/** The largest event sequence observed. */
export const selectHighest = (state: ActivityState): number => state.highest;
export const selectTerminal = (state: ActivityState) => state.terminal;

/** Holes in what has been observed: below `highest`, plus `(highest, watermark]` when the server's watermark is known. */
export function selectMissingRanges(state: ActivityState, watermark = 0): SequenceRange[] {
  const missing: SequenceRange[] = [];
  let next = 1;
  for (const [from, to] of state.ranges) {
    if (from > next) missing.push([next, from - 1]);
    next = to + 1;
  }
  if (watermark >= next) missing.push([next, watermark]);
  return missing;
}

/** Every call, including those folded into groups, in order. */
export function selectCalls(state: ActivityState): CallEntry[] {
  return state.view.flatMap((entry) => entry.kind === "call" ? [entry] : entry.kind === "group" ? entry.calls : []);
}

/** One call by its persisted tool-call id, wherever it sits. */
export function selectCall(state: ActivityState, toolCallId: string): CallEntry | undefined {
  return selectCalls(state).find((call) => call.toolCallId === toolCallId);
}
