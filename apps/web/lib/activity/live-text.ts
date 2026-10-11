/**
 * The client side of live text: the words a model is writing, which arrive as `message.delta` frames over SSE and can be
 * re-read whole from the stream snapshot. Implements the reader rule of the live-text contract exactly:
 *
 *  - a frame older than the current (epoch, revision) is stale and ignored;
 *  - a newer (epoch, revision) replaces the text, but only when the frame is a reset (or starts at offset 0);
 *  - at the same (epoch, revision) a frame appends iff its offset is where the text ends; a frame whose range is already
 *    held is a duplicate; a frame that starts beyond the end means a frame was missed, so the caller must fetch the snapshot.
 *
 * Offsets count Unicode code points: the server measures `len(text)` in Python, where an emoji is one, while a JS string
 * counts it as two UTF-16 units.
 *
 * Durable notes and the saved answer stay authoritative: `visibleLive` hides live text once its durable counterpart is
 * present, and shows it unchanged until then, so there is never a blank frame in between and never the same words twice.
 */
import type { LiveDelta, LiveSnapshot, LiveTextKind, LiveTool } from "@/lib/activity/types";

export type LiveState = {
  readonly epoch: number;
  readonly revision: number;
  readonly kind: LiveTextKind;
  readonly text: string;
  /** `text` measured in code points, kept so appending never re-measures the whole text. */
  readonly length: number;
  /** Which call a `tool` (being written) or `output` (running) text belongs to. Absent for prose, so prose states stay as they were. */
  readonly tool?: LiveTool;
};

/** The isolated kinds: text that belongs to a tool call, never prose for the person to read. */
export const isToolKind = (kind: LiveTextKind): kind is "tool" | "output" => kind === "tool" || kind === "output";

export type LiveStatus = "applied" | "reset" | "duplicate" | "stale" | "mismatch";

export type LiveUpdate = {
  state: LiveState;
  status: LiveStatus;
  /** The frame cannot be applied without the text before it: fetch the stream snapshot and call `applySnapshot`. */
  needsSnapshot: boolean;
};

export const emptyLive: LiveState = { epoch: 0, revision: 0, kind: "progress", text: "", length: 0 };

export const createLiveState = (): LiveState => emptyLive;

const surrogate = /[\uD800-\uDFFF]/;
const pairs = /[\uD800-\uDBFF][\uDC00-\uDFFF]/g;

/** The number of code points in a string (what the server calls its length). */
export function codePointLength(text: string): number {
  if (!surrogate.test(text)) return text.length;
  return text.length - (text.match(pairs)?.length ?? 0); // each surrogate pair is one code point

}

/** The part of a string after its first `count` code points. */
export function skipCodePoints(text: string, count: number): string {
  if (count <= 0) return text;
  if (!surrogate.test(text)) return text.slice(count);
  let index = 0;
  for (let skipped = 0; skipped < count && index < text.length; skipped++) {
    const code = text.charCodeAt(index);
    index += code >= 0xd800 && code <= 0xdbff && index + 1 < text.length ? 2 : 1;
  }
  return text.slice(index);
}

/** Orders (epoch, revision) pairs: negative when `a` is older than `b`. */
const compare = (a: { epoch: number; revision: number }, b: { epoch: number; revision: number }) => a.epoch - b.epoch || a.revision - b.revision;

type Versioned = { epoch: number; revision: number; kind: LiveTextKind; tool?: string | null; call_id?: string | null; detail?: string | null };

/** The call a frame or snapshot names, or undefined for prose (and for a tool row that does not say which call it is). */
function toolOf(version: Versioned): LiveTool | undefined {
  if (!isToolKind(version.kind) || typeof version.tool !== "string" || typeof version.call_id !== "string") return undefined;
  return { name: version.tool, callId: version.call_id, detail: typeof version.detail === "string" ? version.detail : null };
}

const replaced = (version: Versioned, text: string): LiveState => {
  const state = { epoch: version.epoch, revision: version.revision, kind: version.kind, text, length: codePointLength(text) };
  const tool = toolOf(version);
  return tool ? { ...state, tool } : state;
};

/** Applies one `message.delta` frame. Never throws; the returned state is the same object when nothing changed. */
export function reduceLive(state: LiveState, delta: LiveDelta): LiveUpdate {
  const order = compare(delta, state);
  if (order < 0) return { state, status: "stale", needsSnapshot: false };
  if (order > 0) {
    // A newer segment or a non-append change: it stands on its own only when it carries the whole text.
    if (delta.reset || delta.offset === 0) return { state: replaced(delta, delta.text), status: "reset", needsSnapshot: false };
    return { state, status: "mismatch", needsSnapshot: true };
  }
  if (delta.offset > state.length) return { state, status: "mismatch", needsSnapshot: true };
  const end = delta.offset + codePointLength(delta.text);
  if (end <= state.length) return { state, status: "duplicate", needsSnapshot: false };
  // Appends at the end, or overlaps what is held (a repeated frame that also grew): take only the new part.
  const text = state.text + skipCodePoints(delta.text, state.length - delta.offset);
  // The call a tool frame names is fixed for its (epoch, revision), so an append keeps the one the reset that opened it gave.
  return { state: { ...state, kind: delta.kind, text, length: end }, status: "applied", needsSnapshot: false };
}

/**
 * Takes in the whole live text as the server holds it now. A newer (epoch, revision) always wins; at the same version the
 * snapshot wins unless it is shorter than what the stream already delivered (it was read a moment earlier).
 */
export function applySnapshot(state: LiveState, snapshot: LiveSnapshot): LiveState {
  const order = compare(snapshot, state);
  if (order < 0) return state;
  if (order === 0 && codePointLength(snapshot.text) < state.length) return state;
  if (order === 0 && snapshot.text === state.text && snapshot.kind === state.kind) return state;
  return replaced(snapshot, snapshot.text);
}

/**
 * A `message.delta` frame from its parsed JSON, or null when it is not one. Fields an older server does not send get the
 * values that make the frame behave as it always did: revision 0, progress text, not a reset.
 */
export function parseLiveDelta(value: unknown): LiveDelta | null {
  if (typeof value !== "object" || value === null) return null;
  const frame = value as Record<string, unknown>;
  if (typeof frame.task_id !== "string" || typeof frame.text !== "string") return null;
  const whole = (field: unknown, fallback: number) => typeof field === "number" && Number.isInteger(field) && field >= 0 ? field : fallback;
  const epoch = whole(frame.epoch, -1);
  const offset = whole(frame.offset, -1);
  if (epoch < 0 || offset < 0) return null;
  const kind: LiveTextKind = frame.kind === "answer" || frame.kind === "tool" || frame.kind === "output" ? frame.kind : "progress";
  const delta: LiveDelta = {
    task_id: frame.task_id, epoch, revision: whole(frame.revision, 0), offset, text: frame.text, kind, reset: frame.reset === true,
  };
  if (!isToolKind(kind)) return delta;
  // A call's text means nothing without the call it belongs to: a frame that does not say is not shown.
  if (typeof frame.tool !== "string" || typeof frame.call_id !== "string") return null;
  return { ...delta, tool: frame.tool, call_id: frame.call_id, detail: typeof frame.detail === "string" ? frame.detail : null };
}

export type LiveHandoff = {
  /** The live text to show, or "" when it is empty or has been handed off. */
  text: string;
  /** What now carries these words: a saved progress note, the saved answer, or nothing (the live text is still the only copy). */
  handedOffTo: "note" | "answer" | null;
};

/**
 * What to show of the live text. It is hidden only once its durable counterpart exists: a note saved from this epoch or a
 * later one (`committedEpoch`), or the task's saved answer (`savedAnswer`, non-empty once the message is stored). Until
 * then the text is returned unchanged, so the swap to the durable copy never shows a gap or the words twice.
 */
export function liveHandoff(live: LiveState, committedEpoch: number, savedAnswer?: string | null): LiveHandoff {
  // A tool call's text is not words for the person to read; it is shown by the timeline (see `liveToolOf`).
  if (!live.text || isToolKind(live.kind)) return { text: "", handedOffTo: null };
  if (savedAnswer && savedAnswer.trim()) return { text: "", handedOffTo: "answer" };
  if (live.epoch > 0 && live.epoch <= committedEpoch) return { text: "", handedOffTo: "note" };
  return { text: live.text, handedOffTo: null };
}

/** A tool call shown live: the call, whether it is still being written (`tool`) or running (`output`), and its text so far. */
export type LiveToolView = LiveTool & { phase: "tool" | "output"; text: string };

/** The tool call the live row shows, or null when it holds prose, nothing, or a tool row that does not say which call it is. */
export function liveToolOf(live: LiveState): LiveToolView | null {
  return live.tool && isToolKind(live.kind) ? { ...live.tool, phase: live.kind, text: live.text } : null;
}

/** `liveHandoff(...).text`. */
export const visibleLive = (live: LiveState, committedEpoch: number, savedAnswer?: string | null): string =>
  liveHandoff(live, committedEpoch, savedAnswer).text;
