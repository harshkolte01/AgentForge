/**
 * The rules of sending a message and following it into the thread, without React or the network: idempotent resubmission,
 * reconciling from the accepted receipt, never letting a late response touch another conversation, and waiting (retryably)
 * for the saved answer. The workspace, composer and thread call these; they carry the tests.
 */
import type { ConversationMessage, ConversationSession, TurnInput, TurnResult } from "@/lib/workspace-api";

// ---------------------------------------------------------------------------------------------------------------------
// Idempotent submissions

export type Submission = { fingerprint: string; key: string };

/** What identifies a message to the server: everything but the key. Two sends with the same fingerprint are the same message. */
export const fingerprintOf = (input: Omit<TurnInput, "idempotency_key">): string => JSON.stringify(input);

/**
 * The submission to send for `input`: the previous one when nothing changed since (a retry reuses its idempotency key, so the
 * server returns the original receipt instead of making a second turn), a new one when the message changed.
 */
export function nextSubmission(previous: Submission | null, input: Omit<TurnInput, "idempotency_key">, makeKey: () => string): Submission {
  const fingerprint = fingerprintOf(input);
  return previous?.fingerprint === fingerprint ? previous : { fingerprint, key: makeKey() };
}

export type SendBlock = "disabled" | "archived" | "busy" | "running" | "unconfigured" | "empty";

/** A run in progress blocks sending unless the server queues messages (`conversation_queue_enabled`); only an explicit `false` blocks. */
export const runBlocksSending = (running: boolean, queueEnabled: boolean | undefined): boolean => running && queueEnabled === false;

/**
 * Why the composer cannot send right now, or null. Typing is never blocked by a run: only sending is, and Stop stays available.
 * (`disabled` covers loading the workspace; `busy` a send or repository change in flight; `runBlocks` see `runBlocksSending`.)
 */
export function sendBlock(state: { disabled: boolean; archived: boolean; busy: boolean; runBlocks: boolean; configured: boolean; goal: string }): SendBlock | null {
  if (state.archived) return "archived";
  if (state.disabled) return "disabled";
  if (state.busy) return "busy";
  if (state.runBlocks) return "running";
  if (!state.configured) return "unconfigured";
  if (!state.goal.trim()) return "empty";
  return null;
}

/** Whether the field accepts typing. Only a conversation that cannot take messages, or one still loading, locks it. */
export const canType = (state: { disabled: boolean; archived: boolean; busy: boolean }): boolean => !(state.disabled || state.archived || state.busy);

/** A stop request lasts only as long as the run it was made for, so the next run's Stop button works again. */
export function isStopping(stoppedRun: string | null, runId: string | null, running: boolean): boolean {
  return running && stoppedRun !== null && stoppedRun === (runId ?? "current");
}

// ---------------------------------------------------------------------------------------------------------------------
// Pending turn and receipt

/** The message just sent, shown at once. `key` is the client turn id (the idempotency key) and is its row's React key for life. */
export type PendingTurn = { key: string; sessionId: string | null; goal: string; attachments: { name: string }[] };

export const turnRowKey = (clientKey: string): string => `turn:${clientKey}`;

/** The pending turn, if it belongs to this conversation. A late send never paints its bubble in another one. */
export const pendingFor = (pending: PendingTurn | null, sessionId: string): PendingTurn | null => (pending && pending.sessionId === sessionId ? pending : null);

export type Receipt = { key: string; result: TurnResult };

export type ReceiptEffects = {
  sessionId: string;
  /** The messages the receipt carries: the saved user message, and the answer for a replay or an instant reply. */
  messages: ConversationMessage[];
  /** The task now running for this turn, when the receipt says one is. */
  activeTaskId: string | null;
  /** Accepted but waiting its turn (or blocked): there is no run yet. */
  queued: boolean;
  replayed: boolean;
};

/** What an accepted turn means for the conversation, from the receipt alone (no further request needed). */
export function receiptEffects(result: TurnResult, isTerminal: (status: string) => boolean): ReceiptEffects {
  const messages = [result.user_message, result.assistant_message].filter((entry): entry is ConversationMessage => !!entry);
  const running = !!result.task && !result.assistant_message && !isTerminal(result.task.status);
  const waiting = result.state === "queued" || result.state === "blocked";
  return { sessionId: result.session_id, messages, activeTaskId: running && !waiting ? result.task!.id : null, queued: waiting, replayed: result.replayed };
}

/**
 * The conversation as the receipt alone shows it. Applied before the follow-up read of the session, so a failed read leaves the
 * accepted turn visible and its run followed. A replay of an earlier turn does not claim the conversation's active task.
 */
export function sessionAfterReceipt(session: ConversationSession, effects: ReceiptEffects): ConversationSession {
  const newest = Math.max(session.last_sequence, ...effects.messages.map((entry) => entry.sequence));
  const active = effects.replayed || !effects.activeTaskId ? session.active_task_id : effects.activeTaskId;
  if (newest === session.last_sequence && active === session.active_task_id) return session;
  return { ...session, last_sequence: newest, active_task_id: active };
}

/** After a send: move to the conversation the server put the message in, only if the reader is still where they sent it from. */
export function navigationAfterSend(input: { routeAtSend: string | null; routeNow: string | null; resultSessionId: string }): "navigate" | "stay" {
  return input.routeNow === input.routeAtSend && input.routeAtSend !== input.resultSessionId ? "navigate" : "stay";
}

export const announcements = {
  sent: "Message sent.",
  sentWorking: "Message sent. Working on your reply.",
  working: "Working on your reply.",
  ready: "Reply ready.",
  queued: "Message queued.",
} as const;

export const announcementForReceipt = (effects: ReceiptEffects): string =>
  effects.queued ? announcements.queued : effects.activeTaskId ? announcements.sentWorking : announcements.sent;

// ---------------------------------------------------------------------------------------------------------------------
// Never let a late response touch another conversation

export type Ticket = { readonly signal: AbortSignal; isCurrent(): boolean };

/**
 * Everything asynchronous in a conversation (a send, a session read, a message read, a saved-answer read, a repository change)
 * takes a ticket and checks it before touching state or the page. `reset()` (navigating away, unmounting) aborts the requests
 * and makes every outstanding ticket stale at once, so a delayed response can no longer change anything.
 */
export function createScope(): { ticket(): Ticket; reset(): void } {
  let controller = new AbortController();
  return {
    ticket() {
      const own = controller;
      return { signal: own.signal, isCurrent: () => controller === own && !own.signal.aborted };
    },
    reset() {
      controller.abort();
      controller = new AbortController();
    },
  };
}

/** A purpose where only the newest request matters (a refresh while one is in flight): beginning one cancels the previous. */
export function createLatest(scope?: { ticket(): Ticket }): { begin(): Ticket; cancel(): void } {
  let controller: AbortController | null = null;
  return {
    begin() {
      controller?.abort();
      const own = new AbortController();
      controller = own;
      const parent = scope?.ticket();
      parent?.signal.addEventListener("abort", () => own.abort(), { once: true });
      return { signal: own.signal, isCurrent: () => controller === own && !own.signal.aborted && (parent ? parent.isCurrent() : true) };
    },
    cancel() { controller?.abort(); controller = null; },
  };
}

// ---------------------------------------------------------------------------------------------------------------------
// Waiting for the saved answer

/** Pauses before each read of the saved answer: immediately, then backing off. About twelve seconds in all before it asks for a retry. */
export const SAVED_ANSWER_DELAYS: readonly number[] = [0, 400, 800, 1600, 3200, 6000];

export type SavedAnswerWait =
  | { status: "found"; message: ConversationMessage }
  | { status: "gave_up"; error: unknown }
  | { status: "stale" };

/**
 * Reads until the saved assistant message of `taskId` is in the records. Each read may fail or come back without it; both are
 * retried with backoff. It never reports success without the message, and reports `gave_up` (so the person can retry) rather
 * than waiting forever. `stale` means the conversation changed underneath it and nothing should be touched.
 */
export async function waitForSavedAnswer(options: {
  taskId: string;
  /** Reads the conversation's newest messages and merges them into the records; returns the saved answer once it is there. */
  read: () => Promise<ConversationMessage | undefined>;
  sleep: (ms: number) => Promise<void>;
  isCurrent: () => boolean;
  delays?: readonly number[];
}): Promise<SavedAnswerWait> {
  const delays = options.delays ?? SAVED_ANSWER_DELAYS;
  let error: unknown = null;
  for (const delay of delays) {
    if (delay > 0) await options.sleep(delay);
    if (!options.isCurrent()) return { status: "stale" };
    try {
      const message = await options.read();
      if (!options.isCurrent()) return { status: "stale" };
      if (message) return { status: "found", message };
      error = null;
    } catch (failed) {
      if (!options.isCurrent()) return { status: "stale" };
      error = failed;
    }
  }
  return { status: "gave_up", error };
}

// ---------------------------------------------------------------------------------------------------------------------
// Live turn and handoff

/**
 * Which task the thread shows as running, and whether the saved answer has taken over.
 *
 * The run is followed until BOTH its activity is complete (`terminal`: `onTerminal` fired) and its saved answer is in the
 * records: only then does the thread swap the live turn for the saved message, in one commit. If the conversation has stopped
 * reporting the task as active and the swap still has not happened after a grace period (`abandoned`), the saved message is
 * shown as it is rather than hiding it behind a live view that cannot finish.
 */
export function turnPhase(input: { tracked: string | null; saved: ReadonlySet<string>; terminal: ReadonlySet<string>; abandoned: ReadonlySet<string> }): { show: boolean; savedReady: boolean } {
  const { tracked } = input;
  if (!tracked) return { show: false, savedReady: false };
  const savedReady = input.saved.has(tracked);
  const handedOff = savedReady && input.terminal.has(tracked);
  return { show: !handedOff && !input.abandoned.has(tracked), savedReady };
}
