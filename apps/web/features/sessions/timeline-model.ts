/**
 * The pure parts of the activity timeline: what to say when the connection struggles, what a screen reader is told, how a
 * finished (or abandoned) run is projected, and how entries are laid out in sections. Kept free of React so every rule here
 * has a plain node test.
 */
import { describeCall, isDetailable } from "@/lib/activity/presentation";
import type { ActivityEntry, CallEntry, PhaseEntry } from "@/lib/activity/types";
import type { SyncSnapshot } from "@/lib/activity/sync";
import type { LiveState } from "@/lib/activity/live-text";

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
  if (sync.phase === "catching_up" && sync.loading && !sync.awaitingAnswer) return { kind: "catching_up", text: "Catching up…" };
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
  if (live.text) return held && held.epoch === live.epoch && held.kind === live.kind && held.text === live.text ? held : { epoch: live.epoch, kind: live.kind, text: live.text };
  return over ? held : null;
}

/** The held text as live state, for `visibleLive`. */
export function heldAsLive(held: HeldLive): LiveState {
  return { epoch: held.epoch, revision: 0, kind: held.kind, text: held.text, length: held.text.length };
}
