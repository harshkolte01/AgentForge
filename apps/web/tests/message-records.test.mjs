import assert from "node:assert/strict";
import { test } from "node:test";
import { assistantFor, hasEarlier, mergeRecords, PAGE, readEarlier, readForward, sameMessage, savedTaskIds, tailCursor } from "../lib/message-records.ts";
import { createEntranceLedger } from "../lib/entrance-ledger.ts";

const message = (sequence, extra = {}) => ({
  id: `m${sequence}`, session_id: "s", sequence, role: sequence % 2 ? "user" : "assistant", content: `text ${sequence}`,
  task_id: sequence % 2 ? null : `t${sequence}`, created_at: "2026-01-01T00:00:00Z", ...extra,
});
const range = (from, to) => Array.from({ length: to - from + 1 }, (_, i) => message(from + i));

/** A fake API over `all` that pages by after_sequence exactly like the server, and counts requests. */
function server(all, { fail = () => false } = {}) {
  const calls = [];
  const fetchPage = async (after, limit) => {
    calls.push({ after, limit });
    if (fail(calls.length)) throw new Error("network");
    return all.filter((entry) => entry.sequence > after).slice(0, limit);
  };
  return { fetchPage, calls };
}

test("sameMessage compares what the thread shows, including activity and attachments", () => {
  assert.equal(sameMessage(message(2), message(2)), true);
  assert.equal(sameMessage(message(2), message(2, { content: "changed" })), false);
  assert.equal(sameMessage(message(1), message(1, { turn_state: "queued" })), false);
  assert.equal(sameMessage(message(2, { activity: { action_count: 1 } }), message(2, { activity: { action_count: 2 } })), false);
  assert.equal(sameMessage(message(1, { attachments: [{ name: "a.txt", characters: 3 }] }), message(1)), false);
});

test("mergeRecords orders by sequence, replaces by sequence and reports what is new", () => {
  const first = mergeRecords([], [message(3), message(1), message(2)]);
  assert.deepEqual(first.records.map((entry) => entry.sequence), [1, 2, 3]);
  assert.deepEqual(first.added.map((entry) => entry.id), ["m3", "m1", "m2"]);
  assert.equal(first.changed, true);
  const second = mergeRecords(first.records, [message(3, { content: "edited" }), message(4)]);
  assert.deepEqual(second.records.map((entry) => entry.content), ["text 1", "text 2", "edited", "text 4"]);
  assert.deepEqual(second.added.map((entry) => entry.id), ["m4"], "an updated message is not new");
});

test("mergeRecords keeps identity when nothing changed, so memoized rows do not render again", () => {
  const { records } = mergeRecords([], range(1, 5));
  const again = mergeRecords(records, range(1, 5).map((entry) => ({ ...entry })));
  assert.equal(again.changed, false);
  assert.equal(again.records, records, "the same array");
  assert.deepEqual(again.added, []);
  const edited = mergeRecords(records, [message(5, { content: "later" })]);
  assert.equal(edited.records[0], records[0], "unchanged messages keep their object");
  assert.notEqual(edited.records[4], records[4]);
  assert.equal(mergeRecords(records, []).records, records);
});

test("merging a receipt and then the same message from a read dedupes by sequence", () => {
  const fromReceipt = mergeRecords([], [message(7, { id: "m7", content: "hello" })]);
  const fromRead = mergeRecords(fromReceipt.records, [message(7, { id: "m7", content: "hello" }), message(8)]);
  assert.deepEqual(fromRead.records.map((entry) => entry.sequence), [7, 8]);
  assert.deepEqual(fromRead.added.map((entry) => entry.id), ["m8"]);
});

test("tailCursor reads the last page again so recent states refresh; a first load starts a page before the newest", () => {
  assert.equal(tailCursor([], 2000), 1900);
  assert.equal(tailCursor([], 30), 0);
  assert.equal(tailCursor(range(1, 150), 150), 50);
});

test("readForward pages until the newest message with no page cap in sight", async () => {
  const all = range(1, 250);
  const { fetchPage, calls } = server(all);
  const read = await readForward(fetchPage, 0);
  assert.equal(read.messages.length, 250);
  assert.equal(read.exhausted, true);
  assert.deepEqual(calls.map((call) => call.after), [0, 100, 200], "more than the old four-page cutoff would have read");
});

test("readForward on a 2,000-message conversation: the first load reads only the latest page", async () => {
  const all = range(1, 2000);
  const { fetchPage, calls } = server(all);
  const read = await readForward(fetchPage, tailCursor([], 2000));
  assert.equal(read.messages.length, 100);
  assert.equal(read.messages[0].sequence, 1901);
  assert.equal(calls.length, 2, "a full page makes it check once more for newer messages");
  assert.equal(read.exhausted, true);
});

test("readForward past the page cap says so and can be continued", async () => {
  const all = range(1, 400);
  const { fetchPage } = server(all);
  const part = await readForward(fetchPage, 0, { maxPages: 2 });
  assert.equal(part.exhausted, false, "not silently complete");
  assert.equal(part.messages.length, 200);
  assert.equal(part.next, 200);
  const rest = await readForward(fetchPage, part.next, { maxPages: 3 });
  assert.equal(rest.exhausted, true);
  assert.equal(rest.messages.length, 200);
});

test("readForward stops when told to and rejects when a page fails", async () => {
  const { fetchPage } = server(range(1, 500));
  let pages = 0;
  const read = await readForward(async (after, limit) => { pages++; return fetchPage(after, limit); }, 0, { stop: () => pages >= 1 });
  assert.equal(read.exhausted, false);
  assert.equal(pages, 1);
  const failing = server(range(1, 500), { fail: (call) => call === 2 });
  await assert.rejects(readForward(failing.fetchPage, 0), /network/);
});

test("readForward never loops on a page that does not advance", async () => {
  const stuck = async () => range(1, PAGE);
  const read = await readForward(stuck, 100);
  assert.equal(read.exhausted, true);
});

test("readEarlier reads the page of history before the first message", async () => {
  const all = range(1, 1000);
  const { fetchPage } = server(all);
  const read = await readEarlier(fetchPage, 901);
  assert.deepEqual([read.messages[0].sequence, read.messages.at(-1).sequence], [801, 900]);
  assert.equal(read.startReached, false);
  const last = await readEarlier(fetchPage, 61);
  assert.deepEqual([last.messages[0].sequence, last.messages.at(-1).sequence], [1, 60]);
  assert.equal(last.startReached, true);
});

test("readEarlier skips a window with a gap instead of stalling", async () => {
  const all = [...range(1, 50), ...range(400, 500)];
  const { fetchPage, calls } = server(all);
  const read = await readEarlier(fetchPage, 400);
  assert.equal(read.messages.at(-1).sequence, 50);
  assert.equal(read.messages[0].sequence, 1);
  assert.equal(read.startReached, true);
  assert.equal(calls.length >= 3, true);
  const none = await readEarlier(server([]).fetchPage, 5);
  assert.deepEqual(none, { messages: [], startReached: true });
});

test("history paging keeps the merged records contiguous and does not duplicate", async () => {
  const all = range(1, 300);
  const { fetchPage } = server(all);
  let records = mergeRecords([], (await readForward(fetchPage, tailCursor([], 300))).messages).records;
  assert.equal(records[0].sequence, 201);
  assert.equal(hasEarlier(records, false), true);
  const older = await readEarlier(fetchPage, records[0].sequence);
  records = mergeRecords(records, older.messages).records;
  assert.equal(records.length, 200);
  const oldest = await readEarlier(fetchPage, records[0].sequence);
  records = mergeRecords(records, oldest.messages).records;
  assert.equal(records.length, 300);
  assert.equal(hasEarlier(records, oldest.startReached), false);
  assert.deepEqual(records.map((entry) => entry.sequence), all.map((entry) => entry.sequence));
});

test("hasEarlier: a conversation that starts at 1, or whose start was found, has nothing earlier", () => {
  assert.equal(hasEarlier([], false), false);
  assert.equal(hasEarlier(range(1, 3), false), false);
  assert.equal(hasEarlier(range(5, 8), false), true);
  assert.equal(hasEarlier(range(5, 8), true), false);
});

test("assistantFor and savedTaskIds find the saved answer of a task", () => {
  const records = range(1, 4);
  assert.equal(assistantFor(records, "t2").id, "m2");
  assert.equal(assistantFor(records, "t9"), undefined);
  assert.deepEqual([...savedTaskIds(records)].sort(), ["t2", "t4"]);
  assert.equal(assistantFor([message(3, { role: "user", task_id: "t2" })], "t2"), undefined, "a user message is not an answer");
});

test("entrance ledger: history never animates, arrivals animate once", () => {
  const ledger = createEntranceLedger();
  ledger.seed(["a", "b"]);
  ledger.arrived(["c"]);
  assert.equal(ledger.shouldAnimate("a"), false);
  assert.equal(ledger.shouldAnimate("c"), true);
  ledger.played("c");
  assert.equal(ledger.shouldAnimate("c"), false, "a remount (the row moved lists) does not replay it");
  ledger.arrived(["c"]);
  assert.equal(ledger.shouldAnimate("c"), false, "even if a later read reports it as new again");
  ledger.arrived(["a"]);
  assert.equal(ledger.shouldAnimate("a"), false, "history stays history");
  assert.equal(ledger.shouldAnimate("never-seen"), false);
});

test("entrance ledger: a reply that streamed live hands over to its saved message without an entrance", () => {
  const ledger = createEntranceLedger();
  ledger.arrived(["saved-1", "saved-2"]);
  ledger.streamed("task-1");
  assert.equal(ledger.shouldAnimate("saved-1", "task-1"), false);
  assert.equal(ledger.shouldAnimate("saved-2", "task-2"), true, "other tasks are unaffected");
});

test("entrance ledger: the pending bubble and its saved message share one identity", () => {
  const ledger = createEntranceLedger();
  ledger.arrived(["turn:abc"]);
  assert.equal(ledger.shouldAnimate("turn:abc"), true);
  ledger.played("turn:abc");
  // The server's message arrives under the same row key: no second entrance.
  ledger.arrived(["turn:abc"]);
  assert.equal(ledger.shouldAnimate("turn:abc"), false);
});
