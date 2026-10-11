/**
 * The thread's message records: merging what several reads return, paging forward to the newest message and backward through
 * history. Pure (requests are injected), so the rules run under test.
 *
 * The API pages by `after_sequence` (messages with a greater sequence, oldest first, at most `PAGE` of them).
 */
import type { ConversationMessage } from "@/lib/workspace-api";

export const PAGE = 100;
/** The most pages one forward read takes before it stops and says so: 25 pages is more than the 2,000-message conversation limit. */
export const MAX_FORWARD_PAGES = 25;
/** Empty windows an earlier-history read skips (a gap in the sequence) before it gives up for now. */
const MAX_EMPTY_WINDOWS = 20;

export type FetchPage = (after: number, limit: number) => Promise<ConversationMessage[]>;

/** Whether two reads of the same message are the same message as far as the thread shows it. */
export function sameMessage(a: ConversationMessage, b: ConversationMessage): boolean {
  return a.id === b.id && a.sequence === b.sequence && a.content === b.content && a.task_id === b.task_id && a.turn_id === b.turn_id
    && a.turn_state === b.turn_state && a.role === b.role && a.created_at === b.created_at
    && JSON.stringify(a.activity ?? null) === JSON.stringify(b.activity ?? null)
    && JSON.stringify(a.attachments ?? null) === JSON.stringify(b.attachments ?? null)
    && JSON.stringify(a.steps ?? null) === JSON.stringify(b.steps ?? null);
}

export type Merged = {
  records: ConversationMessage[];
  /** Messages whose id was not known before. */
  added: ConversationMessage[];
  /** Whether `records` differs from `current` at all; when false it IS `current` (same array, same objects). */
  changed: boolean;
};

/**
 * Merges messages into the records, oldest first, one per sequence (a later read replaces an earlier one).
 * Messages that did not change keep their object, so a memoized row for them does not render again.
 */
export function mergeRecords(current: readonly ConversationMessage[], incoming: readonly ConversationMessage[]): Merged {
  const known = new Set(current.map((entry) => entry.id));
  const bySequence = new Map<number, ConversationMessage>(current.map((entry) => [entry.sequence, entry]));
  const added: ConversationMessage[] = [];
  let changed = false;
  for (const entry of incoming) {
    const previous = bySequence.get(entry.sequence);
    if (previous && sameMessage(previous, entry)) continue;
    if (!known.has(entry.id)) { known.add(entry.id); added.push(entry); }
    bySequence.set(entry.sequence, entry);
    changed = true;
  }
  if (!changed) return { records: current as ConversationMessage[], added: [], changed: false };
  return { records: [...bySequence.values()].sort((a, b) => a.sequence - b.sequence), added, changed: true };
}

/** Where a forward read starts: the last PAGE messages' worth before the newest one known, so recent states (queued, finished) refresh. */
export function tailCursor(records: readonly ConversationMessage[], lastSequence: number, limit = PAGE): number {
  const newest = records.length ? records[records.length - 1].sequence : lastSequence;
  return Math.max(0, newest - limit);
}

export type ForwardRead = {
  messages: ConversationMessage[];
  /** True when the read reached the newest message. False means the page cap was hit: offer "load more" from `next`. */
  exhausted: boolean;
  /** The cursor to continue from. */
  next: number;
};

/** Reads pages after `after` until the newest message (a short page) or the page cap; never silently stops short. */
export async function readForward(fetchPage: FetchPage, after: number, { limit = PAGE, maxPages = MAX_FORWARD_PAGES, stop }: { limit?: number; maxPages?: number; stop?: () => boolean } = {}): Promise<ForwardRead> {
  const messages: ConversationMessage[] = [];
  let cursor = after;
  for (let page = 0; page < maxPages; page++) {
    if (stop?.()) return { messages, exhausted: false, next: cursor };
    const batch = await fetchPage(cursor, limit);
    messages.push(...batch);
    const last = batch[batch.length - 1];
    if (batch.length < limit || !last || last.sequence <= cursor) return { messages, exhausted: true, next: last ? Math.max(cursor, last.sequence) : cursor };
    cursor = last.sequence;
  }
  return { messages, exhausted: false, next: cursor };
}

export type EarlierRead = {
  messages: ConversationMessage[];
  /** No messages exist before the ones read (or before the first, when none were found). */
  startReached: boolean;
};

/**
 * Reads the page of history just before `firstSequence`. Sequences are normally dense, but a gap (messages removed or never
 * visible) can leave a whole window empty; the read keeps moving back so "load earlier" never stalls on one.
 */
export async function readEarlier(fetchPage: FetchPage, firstSequence: number, { limit = PAGE, stop }: { limit?: number; stop?: () => boolean } = {}): Promise<EarlierRead> {
  let before = firstSequence;
  for (let window = 0; window < MAX_EMPTY_WINDOWS; window++) {
    if (stop?.()) return { messages: [], startReached: false };
    const after = Math.max(0, before - 1 - limit);
    const batch = await fetchPage(after, limit);
    const earlier = batch.filter((entry) => entry.sequence < before);
    if (earlier.length) return { messages: earlier, startReached: earlier[0].sequence <= 1 };
    if (after === 0) return { messages: [], startReached: true };
    before = after + 1;
  }
  return { messages: [], startReached: false };
}

/** Whether older messages may exist. Sequences start at 1; `startReached` records that a read found the true start. */
export function hasEarlier(records: readonly ConversationMessage[], startReached: boolean): boolean {
  return !startReached && records.length > 0 && records[0].sequence > 1;
}

/** The saved assistant message of a task, if the records hold it. */
export function assistantFor(records: readonly ConversationMessage[], taskId: string): ConversationMessage | undefined {
  return records.findLast((entry) => entry.role === "assistant" && entry.task_id === taskId);
}

/** Ids of the tasks whose saved assistant message is in the records. */
export function savedTaskIds(records: readonly ConversationMessage[]): ReadonlySet<string> {
  const ids = new Set<string>();
  for (const entry of records) if (entry.role === "assistant" && entry.task_id) ids.add(entry.task_id);
  return ids;
}
