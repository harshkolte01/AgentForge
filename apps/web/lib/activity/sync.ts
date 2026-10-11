/**
 * The recovery controller: keeps one task's activity and live text in step with the server, whatever the network does.
 *
 * Three sources feed it and any of them may repeat or overlap another without harm:
 *  - the SSE stream (durable events, plus `message.delta` live text);
 *  - polling every few seconds (task status; history pages for whatever the stream did not deliver; the stream snapshot
 *    for live text while the stream is not connected);
 *  - the history API, paged, to fill any hole.
 *
 * Rules it enforces (the acceptance bar of the chat hardening plan):
 *  - Recovery is driven by `contiguous`, never by the largest sequence seen, so a hole is filled, not skipped.
 *  - Catch-up of `(contiguous, target]` continues page after page until it is complete. There is no page-count cutoff;
 *    progress is visible as `phase`, `loading`, `retryAt` and `error`.
 *  - The run is `complete` only when the task is terminal AND every event up to its end has been read AND the caller's
 *    saved-answer check passed. A failed fetch keeps it retryable with capped exponential backoff; it never completes early.
 *  - Revocation (`session.closed`) and 401 halt everything and are surfaced; a task that is gone (404) likewise.
 *  - `stop()` aborts requests, closes the stream and clears every timer and subscription.
 *
 * Everything with a side effect is injected (requests, stream, clock, visibility), so it runs under fake timers in tests.
 * The controller owns the activity state (`reducer.ts`) and the live text (`live-text.ts`) and publishes immutable
 * snapshots for `useSyncExternalStore`.
 */
import { createActivityState, reduceActivity, selectContiguous, selectHighest } from "@/lib/activity/reducer";
import { applySnapshot, createLiveState, reduceLive, type LiveState } from "@/lib/activity/live-text";
import type { ActivityOptions, ActivityState, LiveDelta, LiveSnapshot, TaskEvent } from "@/lib/activity/types";

export type SyncPhase = "connecting" | "live" | "catching_up" | "retrying" | "complete" | "unreachable";

export type SyncErrorKind =
  /** The API could not be reached or answered with a failure; retried automatically. */
  | "network"
  /** The server could not supply every event yet (a hole in its log); retried automatically. */
  | "incomplete"
  /** The stream said the session was closed (revoked). Final. */
  | "revoked"
  /** A request was refused with 401. Final. */
  | "unauthorized"
  /** The task is not available to this account (404). Final. */
  | "not_found";

export type SyncError = { kind: SyncErrorKind; message?: string };

/** What the controller needs to know about the task row. `Task` satisfies it. */
export type SyncTask = { status: string; event_sequence: number; cancel_requested_at?: string | null };

export type StreamHandlers = {
  onOpen(): void;
  onEvent(event: TaskEvent): void;
  onDelta(delta: LiveDelta): void;
  /** The server closed the session: access was revoked. */
  onSessionClosed(): void;
  /** The connection ended and will not reopen by itself; the controller reopens it after a pause. */
  onDisconnect(): void;
};

export type StreamConnection = { close(): void };

export type SyncOptions = {
  /** Events per history page (at most the API's limit). */
  pageSize: number;
  /** Pause between status reads while the task runs. */
  pollMs: number;
  /** Debounce before reading status after the stream reported something that changes it. */
  soonMs: number;
  /** Pause before reopening a stream that was refused or closed. */
  reconnectMs: number;
  /** First retry delay after a failure; doubles each time up to `backoffMaxMs`. */
  backoffBaseMs: number;
  backoffMaxMs: number;
  /** First re-check delay while the saved answer is not there yet; doubles up to `backoffMaxMs`. */
  answerBaseMs: number;
};

export const defaultSyncOptions: SyncOptions = {
  pageSize: 100, pollMs: 3000, soonMs: 300, reconnectMs: 2000, backoffBaseMs: 1000, backoffMaxMs: 30_000, answerBaseMs: 500,
};

export type SyncClock = {
  now(): number;
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
};

export const systemClock: SyncClock = {
  now: () => Date.now(),
  setTimeout: (callback, ms) => setTimeout(callback, ms),
  clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

export type SyncDeps<T extends SyncTask = SyncTask> = {
  taskId: string;
  fetchTask(signal: AbortSignal): Promise<T>;
  /** One page of events with a sequence greater than `after`, oldest first. */
  fetchHistory(after: number, limit: number, signal: AbortSignal): Promise<readonly TaskEvent[]>;
  /** The whole live text now; used when a delta cannot be applied and while the stream is not connected. */
  fetchSnapshot?(signal: AbortSignal): Promise<LiveSnapshot>;
  /** Opens the event stream from `after`. Absent when SSE is unavailable: polling carries everything. */
  openStream?(after: number, handlers: StreamHandlers): StreamConnection;
  /** Whether the saved answer for this task is in place. Absent means nothing needs to be waited for. */
  savedAnswerReady?(task: T): boolean | Promise<boolean>;
  isTerminalStatus(status: string): boolean;
  clock?: SyncClock;
  /** Calls the listener with `true` whenever the page becomes visible (a background tab was throttled; read what it missed). */
  onVisible?(listener: () => void): () => void;
  /** The session is gone (revoked or 401): the app should show its sign-in. */
  onSessionLost?(reason: "revoked" | "unauthorized"): void;
  activity?: ActivityOptions;
  options?: Partial<SyncOptions>;
};

export type SyncSnapshot<T extends SyncTask = SyncTask> = {
  phase: SyncPhase;
  /** A request for missing events is in flight. */
  loading: boolean;
  /** When the next automatic retry or re-check happens (clock time), if one is scheduled after a failure or a wait. */
  retryAt: number | null;
  /** Consecutive failures since the last success. */
  attempts: number;
  error: SyncError | null;
  task: T | null;
  /** The task row says it is over. */
  terminal: boolean;
  /** Terminal and every event read, but the saved answer is not in place yet. */
  awaitingAnswer: boolean;
  /** The SSE connection is open. */
  streaming: boolean;
  /** Nothing more will be tried (revoked, unauthorized, not found). */
  halted: boolean;
  /** The server's event watermark as last read. */
  target: number;
  /** When something new last showed (an event or live text), on the clock. */
  lastProgressAt: number;
  activity: ActivityState;
  live: LiveState;
};

export type SyncController<T extends SyncTask = SyncTask> = {
  start(): void;
  /** Aborts requests, closes the stream, clears timers. The last snapshot stays readable. Safe to call repeatedly. */
  stop(): void;
  /** Reads again now, skipping any wait (a "Retry" button). */
  retryNow(): void;
  getSnapshot(): SyncSnapshot<T>;
  subscribe(listener: () => void): () => void;
};

const statusOf = (error: unknown): number | undefined => (error as { status?: unknown } | null)?.status as number | undefined;
const messageOf = (error: unknown): string | undefined => error instanceof Error ? error.message : undefined;

export function createSyncController<T extends SyncTask = SyncTask>(deps: SyncDeps<T>): SyncController<T> {
  const options: SyncOptions = { ...defaultSyncOptions, ...deps.options };
  const clock = deps.clock ?? systemClock;
  const abort = new AbortController();
  const listeners = new Set<() => void>();
  let snapshot: SyncSnapshot<T> = {
    phase: "connecting", loading: false, retryAt: null, attempts: 0, error: null, task: null, terminal: false, awaitingAnswer: false,
    streaming: false, halted: false, target: 0, lastProgressAt: clock.now(),
    activity: createActivityState(deps.taskId, deps.activity), live: createLiveState(),
  };
  let started = false;
  let stopped = false;
  let inFlight = false;
  let rerun = false;
  let timer: { handle: unknown; at: number } | null = null;
  let reconnectHandle: unknown = null;
  let connection: StreamConnection | null = null;
  let stopVisibility: (() => void) | null = null;
  let snapshotInFlight = false;
  let needsSnapshot = false;
  let answerAttempts = 0;

  const done = () => stopped || snapshot.halted || snapshot.phase === "complete";

  function set(patch: Partial<SyncSnapshot<T>>) {
    if (stopped) return;
    snapshot = { ...snapshot, ...patch };
    for (const listener of [...listeners]) listener();
  }

  // Timers ---------------------------------------------------------------------------------------------------------------

  function cancelTimer() {
    if (timer) clock.clearTimeout(timer.handle);
    timer = null;
  }
  /** Runs the next read at `at`, unless one is already due sooner. */
  function arm(at: number) {
    if (done()) return;
    if (timer && timer.at <= at) return;
    cancelTimer();
    const handle = clock.setTimeout(() => { timer = null; void sync(); }, Math.max(0, at - clock.now()));
    timer = { handle, at };
  }
  /** A read soon: right away if idle, after the current one otherwise. */
  function soon(delay = options.soonMs) {
    if (inFlight) { rerun = true; return; }
    arm(clock.now() + delay);
  }
  function backoff(attempts: number) {
    return Math.min(options.backoffMaxMs, options.backoffBaseMs * 2 ** Math.max(0, attempts - 1));
  }

  // Ingest ---------------------------------------------------------------------------------------------------------------

  /** Takes in events from any source. Returns whether anything was new. */
  function ingest(events: readonly TaskEvent[]): boolean {
    const next = reduceActivity(snapshot.activity, { type: "events", events });
    if (next === snapshot.activity) return false;
    set({ activity: next, ...(next.events !== snapshot.activity.events ? { lastProgressAt: clock.now() } : {}) });
    return true;
  }

  function applyLive(delta: LiveDelta) {
    if (delta.task_id !== deps.taskId) return;
    const update = reduceLive(snapshot.live, delta);
    if (update.state !== snapshot.live) set({ live: update.state, ...(delta.text ? { lastProgressAt: clock.now() } : {}) });
    if (update.needsSnapshot) { needsSnapshot = true; void refreshSnapshot(); }
  }

  /** Reads the whole live text. Failures are silent apart from auth: the next poll tries again while `needsSnapshot` stays set. */
  async function refreshSnapshot() {
    if (!deps.fetchSnapshot || snapshotInFlight || done()) return;
    snapshotInFlight = true;
    try {
      const result = await deps.fetchSnapshot(abort.signal);
      if (stopped || result.task_id !== deps.taskId) return;
      const live = applySnapshot(snapshot.live, result);
      needsSnapshot = false;
      if (live !== snapshot.live) set({ live, ...(live.text ? { lastProgressAt: clock.now() } : {}) });
    } catch (error) {
      if (!stopped && isFinal(error)) haltFor(error);
    } finally {
      snapshotInFlight = false;
    }
  }

  // Stream ---------------------------------------------------------------------------------------------------------------

  /** Events that change what the task row says, so the status is read soon after them. */
  const statusEvents = new Set(["task.finished", "task.cancel_requested", "task.status", "task.recovered", "approval.required", "approval.decided"]);

  const handlers: StreamHandlers = {
    onOpen() { if (!stopped) set({ streaming: true }); },
    onEvent(event) {
      if (done()) return;
      ingest([event]);
      const behind = selectContiguous(snapshot.activity) < selectHighest(snapshot.activity);
      if (statusEvents.has(event.event_type) || behind) soon();
    },
    onDelta(delta) { if (!done()) applyLive(delta); },
    onSessionClosed() { haltWith({ kind: "revoked" }); },
    onDisconnect() {
      connection?.close();
      connection = null;
      if (stopped) return;
      set({ streaming: false });
      scheduleReconnect();
    },
  };

  function connect() {
    if (!deps.openStream || connection || done() || snapshot.terminal) return;
    try { connection = deps.openStream(selectContiguous(snapshot.activity), handlers); } catch { scheduleReconnect(); }
  }
  function scheduleReconnect() {
    if (reconnectHandle !== null || done() || snapshot.terminal) return;
    reconnectHandle = clock.setTimeout(() => { reconnectHandle = null; connect(); }, options.reconnectMs);
  }
  function closeStream() {
    connection?.close();
    connection = null;
    if (reconnectHandle !== null) clock.clearTimeout(reconnectHandle);
    reconnectHandle = null;
    if (snapshot.streaming) set({ streaming: false });
  }

  // Failure handling -----------------------------------------------------------------------------------------------------

  const isFinal = (error: unknown) => statusOf(error) === 401 || statusOf(error) === 404;

  function haltWith(error: SyncError) {
    if (stopped || snapshot.halted) return;
    closeStream();
    cancelTimer();
    stopVisibility?.();
    stopVisibility = null;
    set({ halted: true, phase: "unreachable", loading: false, retryAt: null, error, awaitingAnswer: false });
    if (error.kind === "revoked" || error.kind === "unauthorized") deps.onSessionLost?.(error.kind);
  }
  function haltFor(error: unknown) {
    haltWith(statusOf(error) === 401 ? { kind: "unauthorized", message: messageOf(error) } : { kind: "not_found", message: messageOf(error) });
  }

  /** A read failed: surface it, and try again after a capped, doubling pause. */
  function fail(error: SyncError, phase: "retrying" | "unreachable") {
    const attempts = snapshot.attempts + 1;
    const delay = backoff(attempts);
    set({ phase, loading: false, error, attempts, retryAt: clock.now() + delay });
    arm(clock.now() + delay);
  }

  // The read cycle -------------------------------------------------------------------------------------------------------

  /** Pages through history until every event up to `goal` is held, or the server has nothing more to give. */
  async function catchUp(goal: number) {
    if (selectContiguous(snapshot.activity) >= goal) return;
    set({ phase: "catching_up", loading: true });
    try {
      for (;;) {
        const before = selectContiguous(snapshot.activity);
        if (before >= goal) break;
        const page = await deps.fetchHistory(before, options.pageSize, abort.signal);
        if (done()) return;
        if (!page.length) break;
        ingest(page);
        // A page that moved nothing means the log has a hole right after `before`: stop instead of asking forever.
        if (selectContiguous(snapshot.activity) === before) break;
      }
    } finally {
      set({ loading: false });
    }
  }

  async function cycle() {
    cancelTimer();
    let task: T;
    try {
      task = await deps.fetchTask(abort.signal);
    } catch (error) {
      if (done()) return;
      if (isFinal(error)) return haltFor(error);
      return fail({ kind: "network", message: messageOf(error) }, "unreachable");
    }
    if (done()) return;
    const terminal = deps.isTerminalStatus(task.status);
    set({ task, terminal, target: task.event_sequence });
    if (terminal) {
      set({ activity: reduceActivity(snapshot.activity, { type: "terminal", status: task.status, sequence: task.event_sequence }) });
      closeStream();
    }

    try {
      // `highest` as well as the task's watermark: a hole below an event already seen is filled even if the status read is older.
      await catchUp(Math.max(task.event_sequence, selectHighest(snapshot.activity)));
    } catch (error) {
      if (done()) return;
      if (isFinal(error)) return haltFor(error);
      return fail({ kind: "network", message: messageOf(error) }, "retrying");
    }
    if (done()) return;

    if (!terminal) {
      // Live text rides the stream; without it (or after a mismatch) the snapshot keeps it moving.
      if (needsSnapshot || !snapshot.streaming) await refreshSnapshot();
      if (done()) return;
      connect();
      set({ phase: "live", loading: false, error: null, attempts: 0, retryAt: null, awaitingAnswer: false });
      arm(clock.now() + options.pollMs);
      return;
    }

    if (selectContiguous(snapshot.activity) < task.event_sequence) {
      return fail({ kind: "incomplete" }, "retrying");
    }
    let ready = true;
    try {
      ready = deps.savedAnswerReady ? await deps.savedAnswerReady(task) : true;
    } catch (error) {
      if (done()) return;
      if (isFinal(error)) return haltFor(error);
      return fail({ kind: "network", message: messageOf(error) }, "retrying");
    }
    if (done()) return;
    if (!ready) {
      const delay = Math.min(options.backoffMaxMs, options.answerBaseMs * 2 ** answerAttempts++);
      set({ phase: "catching_up", loading: false, error: null, attempts: 0, awaitingAnswer: true, retryAt: clock.now() + delay });
      arm(clock.now() + delay);
      return;
    }
    set({ phase: "complete", loading: false, error: null, attempts: 0, awaitingAnswer: false, retryAt: null });
    cleanup();
  }

  async function sync() {
    if (done()) return;
    if (inFlight) { rerun = true; return; }
    inFlight = true;
    try {
      do {
        rerun = false;
        await cycle();
      } while (rerun && !done());
    } catch (error) {
      // Nothing in a cycle is meant to throw; if something does, it is a failed read like any other.
      if (!done()) fail({ kind: "network", message: messageOf(error) }, "retrying");
    } finally {
      inFlight = false;
    }
  }

  function cleanup() {
    closeStream();
    cancelTimer();
    stopVisibility?.();
    stopVisibility = null;
  }

  return {
    start() {
      if (started || stopped) return;
      started = true;
      stopVisibility = deps.onVisible?.(() => { if (!done()) { cancelTimer(); connect(); void sync(); } }) ?? null;
      // The stream opens once the first catch-up is done, from the contiguous point, so it does not replay what history just gave.
      void sync();
    },
    stop() {
      if (stopped) return;
      cleanup();
      stopped = true;
      abort.abort();
      listeners.clear();
    },
    retryNow() {
      if (stopped || snapshot.phase === "complete") return;
      cancelTimer();
      connect();
      void sync();
    },
    getSnapshot: () => snapshot,
    subscribe(listener) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}

// ---------------------------------------------------------------------------------------------------------------------
// Historical loading

/**
 * Every event of a task from `after` on, page by page until the server has no more: for expanding the activity of a
 * finished reply. There is no page-count cutoff. `pageSize` must not exceed the API's per-page limit (100).
 */
export async function loadActivityHistory(
  fetchHistory: (after: number, limit: number, signal?: AbortSignal) => Promise<readonly TaskEvent[]>,
  { signal, pageSize = defaultSyncOptions.pageSize, after = 0, onPage }: { signal?: AbortSignal; pageSize?: number; after?: number; onPage?: (page: readonly TaskEvent[]) => void } = {},
): Promise<TaskEvent[]> {
  const events: TaskEvent[] = [];
  let cursor = after;
  for (;;) {
    signal?.throwIfAborted();
    const page = await fetchHistory(cursor, pageSize, signal);
    signal?.throwIfAborted();
    if (!page.length) break;
    let last = cursor;
    for (const event of page) { events.push(event); if (event.sequence > last) last = event.sequence; }
    onPage?.(page);
    // No progress (a server that ignores the cursor) or a short page: that was the end.
    if (last === cursor || page.length < pageSize) break;
    cursor = last;
  }
  return events;
}
