/**
 * One conversation's client-side records and everything that happens to them, without React: the first load of the latest page,
 * refreshes that read to the end, earlier history, the accepted-turn receipt, and the hand-off from a live run to its saved
 * answer. The thread subscribes to it (`useSyncExternalStore`); the workspace feeds it receipts.
 *
 * Each store belongs to one conversation. Every request it makes takes a ticket from its scope, so a response that arrives after
 * the store was stopped (the reader moved to another conversation) is dropped instead of applied. Requests, timers and the
 * saved-answer wait are all injected, so the flows run under test with fakes.
 */
import { createEntranceLedger, type EntranceLedger } from "@/lib/entrance-ledger";
import { assistantFor, hasEarlier, mergeRecords, readEarlier, readForward, savedTaskIds, tailCursor, type FetchPage } from "@/lib/message-records";
import { announcementForReceipt, announcements, createLatest, createScope, receiptEffects, SAVED_ANSWER_DELAYS, turnPhase, turnRowKey, waitForSavedAnswer, type Receipt, type ReceiptEffects, type Ticket } from "@/features/sessions/turn-flow";
import type { ConversationMessage, ConversationSession } from "@/lib/workspace-api";

/** How long a run the conversation no longer reports as active may keep the live view before the saved message is shown regardless. */
export const TURN_GRACE_MS = 15_000;

/** What a run that just ended in this view did (the activity entries) and how it ended. Kept so the saved message shows them in place. */
export type Terminal = { entries: readonly unknown[]; status: string; durationMs: number | null };

export type StoreState = {
  /** Oldest first, one per sequence. */
  records: ConversationMessage[];
  /** The first read succeeded. Until then, with no error, the thread shows its loading placeholder. */
  loaded: boolean;
  /** The last read that failed, in words; empty when the latest read worked. Existing messages stay on screen. */
  error: string;
  startReached: boolean;
  /** A forward read stopped at its page cap: messages after this sequence were not read yet. */
  moreAfter: number | null;
  olderBusy: boolean;
  newerBusy: boolean;
  /** React key of a message's row when it differs from its id: a user message keeps the key of the bubble it was sent as. */
  rowKeys: ReadonlyMap<string, string>;
  terminal: ReadonlyMap<string, Terminal>;
  /** The run being followed: kept past the conversation reporting it inactive until its saved answer takes over. */
  tracked: string | null;
  abandoned: ReadonlySet<string>;
  /** The saved answer of a finished run is still being fetched (`failed` after the bounded retries ran out). */
  savedWait: { taskId: string; failed: boolean; error: string } | null;
  /** A short sentence for a polite live region. Changes only when something worth announcing happens. */
  announcement: string;
  /** Set when the first read lands: where the view should go. `id` changes each time it is set. */
  landing: { id: number; turn: boolean } | null;
};

export type StoreDeps = {
  sessionId: string;
  initialSession: ConversationSession;
  fetchSession(signal: AbortSignal): Promise<ConversationSession>;
  fetchPage(after: number, limit: number, signal: AbortSignal): Promise<ConversationMessage[]>;
  /** The conversation as just read: the parent keeps its list in step. */
  onSession(session: ConversationSession): void;
  isTerminal(status: string): boolean;
  describeError(error: unknown): string;
  /** Called right before earlier messages are inserted above the view, to keep the reader's place. */
  beforePrepend?(): void;
  setTimeout?(callback: () => void, ms: number): unknown;
  clearTimeout?(handle: unknown): void;
  savedDelays?: readonly number[];
  graceMs?: number;
};

export type ConversationStore = {
  getState(): StoreState;
  subscribe(listener: () => void): () => void;
  ledger: EntranceLedger;
  /** Begins a lifetime (and may be called again after `stop`). */
  start(): void;
  /** Aborts every request and timer and makes outstanding responses stale. */
  stop(): void;
  /** Reads the conversation and its newest messages. Overlapping calls coalesce into one follow-up read. `light` skips the message read when nothing changed. */
  refresh(options?: { light?: boolean }): Promise<void>;
  loadEarlier(): Promise<void>;
  loadNewer(): Promise<void>;
  /** Reconciles from an accepted turn: the saved user message, an instant or replayed answer, and the run to follow. */
  applyReceipt(receipt: Receipt): ReceiptEffects;
  /** The conversation reported its active task (or none). */
  observeSession(session: ConversationSession): void;
  /** A run's activity is complete through its terminal event: fetch its saved answer until it is there. */
  runFinished(taskId: string, entries: readonly unknown[], status: string, durationMs?: number | null): void;
  retrySaved(): void;
  /** The run's answer appeared live: its saved message must not play an entrance. */
  replyStreamed(taskId: string): void;
  rowKey(message: ConversationMessage): string;
  hasEarlier(): boolean;
  /** Which run the thread shows live, and whether its saved answer is in. `active` is the conversation's own active task. */
  phase(active: string | null): { taskId: string | null; show: boolean; savedReady: boolean };
};

/** The React key of a message's row: its own id, unless it is a user message that keeps the key of the bubble it was sent as. */
export const rowKeyFor = (state: Pick<StoreState, "rowKeys">, message: ConversationMessage): string => state.rowKeys.get(message.id) ?? message.id;

const savedCaches = new WeakMap<ConversationMessage[], ReadonlySet<string>>();
/** Which tasks have a saved answer in the records: computed once per version of the records. */
function savedIdsOf(records: ConversationMessage[]): ReadonlySet<string> {
  let ids = savedCaches.get(records);
  if (!ids) { ids = savedTaskIds(records); savedCaches.set(records, ids); }
  return ids;
}

/** Which run the thread shows live, and whether its saved answer is in. `active` is the conversation's own active task. */
export function phaseOf(state: Pick<StoreState, "records" | "tracked" | "terminal" | "abandoned">, active: string | null): { taskId: string | null; show: boolean; savedReady: boolean } {
  const taskId = active ?? state.tracked;
  const { show, savedReady } = turnPhase({ tracked: taskId, saved: savedIdsOf(state.records), terminal: new Set(state.terminal.keys()), abandoned: state.abandoned });
  return { taskId, show, savedReady };
}

const initialState: StoreState = {
  records: [], loaded: false, error: "", startReached: false, moreAfter: null, olderBusy: false, newerBusy: false, rowKeys: new Map(),
  terminal: new Map(), tracked: null, abandoned: new Set(), savedWait: null, announcement: "", landing: null,
};

export function createConversationStore(deps: StoreDeps): ConversationStore {
  const scope = createScope();
  const saved = createLatest(scope);
  const schedule = deps.setTimeout ?? ((callback, ms) => setTimeout(callback, ms));
  const unschedule = deps.clearTimeout ?? ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>));
  const ledger = createEntranceLedger();
  const listeners = new Set<() => void>();
  let state: StoreState = { ...initialState, tracked: deps.initialSession.active_task_id };
  let running: Ticket | null = null;
  // A refresh asked for while one was in flight: it runs once more afterwards (the lightest request wins only if every ask was light).
  let rerun: { light: boolean } | null = null;
  let seen: { revision: number | undefined; last_sequence: number } | null = null;
  const rowKeyOf = (message: ConversationMessage) => rowKeyFor(state, message);
  let landings = 0;
  let sessionActive: string | null = deps.initialSession.active_task_id;
  let graceTimer: unknown = null;
  let graceFor: string | null = null;

  function set(patch: Partial<StoreState>) {
    state = { ...state, ...patch };
    arm();
    listeners.forEach((listener) => listener());
  }

  /** The grace timer runs only for a followed run the conversation no longer reports as active and whose saved view has not taken over. */
  function arm() {
    const tracked = state.tracked;
    const needed = !!tracked && sessionActive !== tracked && !state.abandoned.has(tracked) && !(savedIdsOf(state.records).has(tracked) && state.terminal.has(tracked));
    if (graceTimer !== null && (!needed || graceFor !== tracked)) { unschedule(graceTimer); graceTimer = null; graceFor = null; }
    if (needed && graceTimer === null) {
      graceFor = tracked;
      graceTimer = schedule(() => {
        graceTimer = null; graceFor = null;
        if (tracked) set({ abandoned: new Set([...state.abandoned, tracked]) });
      }, deps.graceMs ?? TURN_GRACE_MS);
    }
  }

  const pageFetcher = (ticket: Ticket): FetchPage => (after, limit) => deps.fetchPage(after, limit, ticket.signal);

  /** Reads the session and the messages after the tail cursor to the end (or the page cap). Throws on failure. */
  async function readTail(ticket: Ticket, light = false) {
    const session = await deps.fetchSession(ticket.signal);
    if (light && seen && session.revision !== undefined && seen.revision === session.revision && seen.last_sequence === session.last_sequence && state.loaded && state.moreAfter === null) {
      return { session, read: null };
    }
    const after = tailCursor(state.records, session.last_sequence);
    const read = await readForward(pageFetcher(ticket), after, { stop: () => !ticket.isCurrent() });
    return { session, read };
  }

  function commit(ticket: Ticket, session: ConversationSession, read: { messages: ConversationMessage[]; exhausted: boolean; next: number } | null) {
    if (!ticket.isCurrent()) return;
    seen = { revision: session.revision, last_sequence: session.last_sequence };
    const first = !state.loaded;
    const patch: Partial<StoreState> = { loaded: true, error: "" };
    if (read) {
      const merged = mergeRecords(state.records, read.messages);
      const keys = merged.added.map(rowKeyOf);
      if (first) ledger.seed(read.messages.map(rowKeyOf)); else ledger.arrived(keys);
      if (merged.changed) patch.records = merged.records;
      patch.moreAfter = read.exhausted ? null : read.next;
    }
    if (first) patch.landing = { id: ++landings, turn: !!session.active_task_id };
    sessionActive = session.active_task_id;
    if (session.active_task_id && session.active_task_id !== state.tracked) patch.tracked = session.active_task_id;
    // A refresh that finds the saved answer ends the wait for it.
    const wait = state.savedWait;
    if (wait && assistantFor(patch.records ?? state.records, wait.taskId)) { saved.cancel(); patch.savedWait = null; patch.announcement = announcements.ready; }
    set(patch);
    deps.onSession(session);
  }

  async function refresh(options: { light?: boolean } = {}): Promise<void> {
    if (running?.isCurrent()) { rerun = { light: (rerun?.light ?? true) && !!options.light }; return; }
    const ticket = scope.ticket();
    running = ticket;
    try {
      let light = !!options.light;
      for (;;) {
        rerun = null;
        try {
          const { session, read } = await readTail(ticket, light);
          commit(ticket, session, read);
        } catch (failed) {
          if (!ticket.isCurrent()) return;
          set({ error: deps.describeError(failed) });
        }
        const next = rerun as { light: boolean } | null;
        if (!next || !ticket.isCurrent()) return;
        light = next.light;
      }
    } finally {
      if (running === ticket) running = null;
    }
  }

  async function loadEarlier(): Promise<void> {
    const first = state.records[0];
    if (!first || state.olderBusy || !hasEarlier(state.records, state.startReached)) return;
    const ticket = scope.ticket();
    set({ olderBusy: true, error: "" });
    try {
      const read = await readEarlier(pageFetcher(ticket), first.sequence, { stop: () => !ticket.isCurrent() });
      if (!ticket.isCurrent()) return;
      const merged = mergeRecords(state.records, read.messages);
      ledger.seed(read.messages.map(rowKeyOf));
      // The reader's place is captured now, right before the rows are inserted above it.
      if (merged.changed) deps.beforePrepend?.();
      set({ records: merged.changed ? merged.records : state.records, startReached: read.startReached, olderBusy: false });
    } catch (failed) {
      if (ticket.isCurrent()) set({ olderBusy: false, error: deps.describeError(failed) });
    }
  }

  async function loadNewer(): Promise<void> {
    if (state.moreAfter === null || state.newerBusy) return;
    const ticket = scope.ticket();
    set({ newerBusy: true, error: "" });
    try {
      const read = await readForward(pageFetcher(ticket), state.moreAfter, { stop: () => !ticket.isCurrent() });
      if (!ticket.isCurrent()) return;
      const merged = mergeRecords(state.records, read.messages);
      ledger.arrived(merged.added.map(rowKeyOf));
      set({ records: merged.changed ? merged.records : state.records, moreAfter: read.exhausted ? null : read.next, newerBusy: false });
    } catch (failed) {
      if (ticket.isCurrent()) set({ newerBusy: false, error: deps.describeError(failed) });
    }
  }

  function applyReceipt(receipt: Receipt): ReceiptEffects {
    const effects = receiptEffects(receipt.result, deps.isTerminal);
    const rowKeys = new Map(state.rowKeys);
    // The saved user message takes over the bubble it was sent as: same row, so nothing remounts or animates twice.
    rowKeys.set(receipt.result.user_message.id, turnRowKey(receipt.key));
    const merged = mergeRecords(state.records, effects.messages);
    ledger.arrived([turnRowKey(receipt.key), ...effects.messages.filter((entry) => entry.role === "assistant").map((entry) => entry.id)]);
    sessionActive = effects.activeTaskId ?? sessionActive;
    set({ rowKeys, records: merged.changed ? merged.records : state.records, announcement: announcementForReceipt(effects), tracked: effects.activeTaskId ?? state.tracked });
    return effects;
  }

  function observeSession(session: ConversationSession) {
    sessionActive = session.active_task_id;
    if (session.active_task_id && session.active_task_id !== state.tracked) set({ tracked: session.active_task_id });
    else arm();
  }

  async function awaitSaved(taskId: string) {
    const ticket = saved.begin();
    set({ savedWait: { taskId, failed: false, error: "" } });
    const result = await waitForSavedAnswer({
      taskId,
      isCurrent: ticket.isCurrent,
      delays: deps.savedDelays ?? SAVED_ANSWER_DELAYS,
      sleep: (ms) => new Promise<void>((resolve) => {
        const handle = schedule(resolve, ms);
        ticket.signal.addEventListener("abort", () => { unschedule(handle); resolve(); }, { once: true });
      }),
      read: async () => {
        const { session, read } = await readTail(ticket);
        // One commit: the records gain the saved message and the thread swaps the live view for it.
        commit(ticket, session, read);
        return assistantFor(state.records, taskId);
      },
    });
    if (result.status === "found") {
      if (state.savedWait?.taskId === taskId) set({ savedWait: null, announcement: announcements.ready });
    } else if (result.status === "gave_up" && ticket.isCurrent()) {
      set({ savedWait: { taskId, failed: true, error: result.error ? deps.describeError(result.error) : "" } });
    }
  }

  return {
    getState: () => state,
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    ledger,
    start() { arm(); },
    stop() {
      scope.reset();
      running = null; rerun = null;
      if (graceTimer !== null) unschedule(graceTimer);
      graceTimer = null; graceFor = null;
      // A stopped store is still readable but does nothing until started again; any in-flight busy flags are cleared.
      if (state.olderBusy || state.newerBusy) state = { ...state, olderBusy: false, newerBusy: false };
    },
    refresh,
    loadEarlier,
    loadNewer,
    applyReceipt,
    observeSession,
    runFinished(taskId, entries, status, durationMs = null) {
      set({ terminal: new Map(state.terminal).set(taskId, { entries, status, durationMs }) });
      // Already there (a refresh got it first): the swap happens with this very commit. Otherwise fetch it.
      if (assistantFor(state.records, taskId)) { set({ announcement: announcements.ready }); return; }
      void awaitSaved(taskId);
    },
    retrySaved() {
      const wait = state.savedWait;
      if (wait) void awaitSaved(wait.taskId);
    },
    replyStreamed(taskId) { ledger.streamed(taskId); },
    rowKey: rowKeyOf,
    hasEarlier: () => hasEarlier(state.records, state.startReached),
    phase: (active) => phaseOf(state, active),
  };
}
