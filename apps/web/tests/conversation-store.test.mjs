import assert from "node:assert/strict";
import { test } from "node:test";
import { createConversationStore, TURN_GRACE_MS } from "../features/sessions/conversation-store.ts";
import { fakeClock, flush } from "./helpers/clock.mjs";

const TERMINAL = new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"]);
const message = (sequence, extra = {}) => ({
  id: `m${sequence}`, session_id: "s", sequence, role: sequence % 2 ? "user" : "assistant", content: `text ${sequence}`,
  task_id: sequence % 2 ? null : `t${sequence}`, created_at: "2026-01-01T00:00:00Z", ...extra,
});
const range = (from, to) => Array.from({ length: to - from + 1 }, (_, i) => message(from + i));

/** A fake conversation on the server: messages, a session row, request counters and switches to fail or hold requests. */
function fake({ count = 10, active = null } = {}) {
  const server = {
    messages: range(1, count),
    revision: 1,
    active,
    calls: { session: 0, pages: [] },
    failSession: 0,
    failPages: 0,
    held: null,
    session: () => ({ id: "s", project_id: "p", title: "t", last_sequence: server.messages.at(-1)?.sequence ?? 0, active_task_id: server.active, revision: server.revision }),
    add(...messages) { server.messages.push(...messages); server.revision++; },
  };
  server.fetchSession = async () => {
    server.calls.session++;
    if (server.held) await server.held;
    if (server.failSession > 0) { server.failSession--; throw new Error("session down"); }
    return server.session();
  };
  server.fetchPage = async (after, limit) => {
    server.calls.pages.push(after);
    if (server.failPages > 0) { server.failPages--; throw new Error("messages down"); }
    return server.messages.filter((entry) => entry.sequence > after).slice(0, limit);
  };
  return server;
}

function build(server, extra = {}) {
  const clock = fakeClock();
  const sessions = [];
  let prepends = 0;
  const store = createConversationStore({
    sessionId: "s",
    initialSession: server.session(),
    fetchSession: () => server.fetchSession(),
    fetchPage: (after, limit) => server.fetchPage(after, limit),
    onSession: (session) => sessions.push(session),
    isTerminal: (status) => TERMINAL.has(status),
    describeError: (error) => (error instanceof Error ? error.message : "unknown"),
    beforePrepend: () => { prepends++; },
    setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    ...extra,
  });
  store.start();
  return { store, clock, sessions, get prepends() { return prepends; } };
}
const ids = (store) => store.getState().records.map((entry) => entry.sequence);
const receipt = (extra = {}, key = "key-1") => ({ key, result: { session_id: "s", user_message: message(11, { id: "u11" }), task: { id: "t12", status: "RUNNING" }, assistant_message: null, replayed: false, state: "running", ...extra } });

test("opening a 2,000-message conversation reads only the latest page; history is on demand", async () => {
  const server = fake({ count: 2000 });
  const { store, sessions } = build(server);
  await store.refresh();
  const state = store.getState();
  assert.equal(state.records.length, 100);
  assert.equal(state.records[0].sequence, 1901);
  assert.equal(state.loaded, true);
  assert.equal(state.landing.turn, false);
  assert.equal(sessions.length, 1);
  assert.equal(store.hasEarlier(), true);
  assert.deepEqual(server.calls.pages, [1900, 2000], "one page plus the check for newer messages");
  for (const entry of state.records) assert.equal(store.ledger.shouldAnimate(store.rowKey(entry)), false, "history never animates");
});

test("opening a conversation with a running turn lands on that turn", async () => {
  const server = fake({ count: 4, active: "t5" });
  const { store } = build(server);
  await store.refresh();
  assert.equal(store.getState().landing.turn, true);
  assert.equal(store.getState().tracked, "t5");
});

test("earlier history pages in without duplicates, keeps the place hook ahead of the insert, and stops at the start", async () => {
  const server = fake({ count: 250 });
  const harness = build(server);
  const { store } = harness;
  await store.refresh();
  assert.equal(ids(store)[0], 151);
  const states = [];
  store.subscribe(() => states.push(store.getState().records.length));
  await store.loadEarlier();
  assert.equal(harness.prepends, 1);
  assert.equal(ids(store)[0], 51);
  await store.loadEarlier();
  assert.equal(ids(store)[0], 1);
  assert.equal(store.hasEarlier(), false);
  assert.equal(ids(store).length, 250);
  assert.equal(new Set(ids(store)).size, 250);
  await store.loadEarlier();
  assert.equal(harness.prepends, 2, "nothing left to load");
  assert.equal(store.getState().olderBusy, false);
});

test("a failed earlier-history read reports the error and keeps what is on screen", async () => {
  const server = fake({ count: 250 });
  const { store } = build(server);
  await store.refresh();
  server.failPages = 1;
  await store.loadEarlier();
  assert.equal(store.getState().error, "messages down");
  assert.equal(store.getState().olderBusy, false);
  assert.equal(ids(store)[0], 151);
  await store.loadEarlier();
  assert.equal(ids(store)[0], 51);
});

test("a refresh reads all the way to the newest message, far beyond the old four-page cutoff", async () => {
  const server = fake({ count: 100 });
  const { store } = build(server);
  await store.refresh();
  server.add(...range(101, 700));
  await store.refresh();
  assert.equal(store.getState().records.at(-1).sequence, 700);
  assert.equal(store.getState().moreAfter, null);
});

test("past the page cap the thread says there is more, and load more continues", async () => {
  const server = fake({ count: 100 });
  const { store } = build(server);
  await store.refresh();
  server.add(...range(101, 3100));
  await store.refresh();
  const capped = store.getState();
  assert.notEqual(capped.moreAfter, null, "not silently complete");
  assert.equal(capped.records.at(-1).sequence < 3100, true);
  await store.loadNewer();
  assert.equal(store.getState().records.at(-1).sequence, 3100);
  assert.equal(store.getState().moreAfter, null);
});

test("new messages that arrive after the first load animate once; history does not", async () => {
  const server = fake({ count: 4 });
  const { store } = build(server);
  await store.refresh();
  server.add(message(5), message(6));
  await store.refresh();
  assert.equal(store.ledger.shouldAnimate("m4"), false);
  assert.equal(store.ledger.shouldAnimate("m5"), true);
  store.ledger.played("m5");
  await store.refresh();
  assert.equal(store.ledger.shouldAnimate("m5"), false, "a refresh does not replay it");
});

test("refreshing an unchanged conversation keeps the same records array, so nothing re-renders", async () => {
  const server = fake({ count: 6 });
  const { store } = build(server);
  await store.refresh();
  const before = store.getState().records;
  await store.refresh();
  assert.equal(store.getState().records, before);
});

test("overlapping refreshes coalesce into one follow-up read", async () => {
  const server = fake({ count: 6 });
  const { store } = build(server);
  let release;
  server.held = new Promise((resolve) => { release = resolve; });
  const first = store.refresh();
  await flush();
  const second = store.refresh();
  const third = store.refresh();
  await flush();
  assert.equal(server.calls.session, 1, "one request in flight");
  server.held = null;
  release();
  await Promise.all([first, second, third]);
  assert.equal(server.calls.session, 2, "exactly one more read, not two");
});

test("a light refresh skips the message read when the conversation did not change", async () => {
  const server = fake({ count: 6 });
  const { store } = build(server);
  await store.refresh();
  const pages = server.calls.pages.length;
  await store.refresh({ light: true });
  assert.equal(server.calls.pages.length, pages, "only the session was read");
  server.add(message(7));
  await store.refresh({ light: true });
  assert.equal(store.getState().records.at(-1).sequence, 7);
});

test("a failed refresh keeps the messages and offers a retry; the next one clears it", async () => {
  const server = fake({ count: 6 });
  const { store } = build(server);
  await store.refresh();
  server.failSession = 1;
  await store.refresh();
  assert.equal(store.getState().error, "session down");
  assert.equal(ids(store).length, 6);
  await store.refresh();
  assert.equal(store.getState().error, "");
});

test("a first load that fails reports it and the retry loads", async () => {
  const server = fake({ count: 6 });
  server.failSession = 1;
  const { store } = build(server);
  await store.refresh();
  assert.equal(store.getState().error, "session down");
  assert.equal(store.getState().records.length, 0);
  await store.refresh();
  assert.equal(store.getState().records.length, 6);
  assert.equal(store.getState().landing.id, 1);
});

test("stopping the store drops responses that arrive afterwards: nothing leaks into the next conversation", async () => {
  const server = fake({ count: 6 });
  const { store, sessions } = build(server);
  let release;
  server.held = new Promise((resolve) => { release = resolve; });
  const pending = store.refresh();
  await flush();
  store.stop();
  release();
  await pending;
  assert.equal(store.getState().records.length, 0);
  assert.equal(sessions.length, 0, "the parent was not told about a conversation that was left");
});

test("a store can be stopped and started again (a development remount) and works", async () => {
  const server = fake({ count: 6 });
  const { store } = build(server);
  const first = store.refresh();
  store.stop();
  store.start();
  await first;
  await store.refresh();
  assert.equal(store.getState().records.length, 6);
});

test("a receipt shows the accepted turn at once and follows its run, even if reading the session fails", async () => {
  const server = fake({ count: 10 });
  const { store } = build(server);
  await store.refresh();
  server.failSession = 5;
  const effects = store.applyReceipt(receipt());
  assert.equal(effects.activeTaskId, "t12");
  const state = store.getState();
  assert.equal(state.records.at(-1).id, "u11");
  assert.equal(state.tracked, "t12");
  assert.equal(state.announcement, "Message sent. Working on your reply.");
  await store.refresh();
  assert.equal(store.getState().error, "session down");
  assert.equal(store.getState().records.at(-1).id, "u11", "the accepted message stays");
  assert.equal(store.getState().tracked, "t12");
});

test("the saved user message keeps the row key of the bubble it was sent as, and does not animate again", async () => {
  const server = fake({ count: 10 });
  const { store } = build(server);
  await store.refresh();
  store.applyReceipt(receipt());
  const user = store.getState().records.at(-1);
  assert.equal(store.rowKey(user), "turn:key-1");
  assert.equal(store.ledger.shouldAnimate("turn:key-1"), true, "the bubble's one entrance");
  store.ledger.played("turn:key-1");
  server.add(message(11, { id: "u11" }));
  await store.refresh();
  assert.equal(store.rowKey(store.getState().records.at(-1)), "turn:key-1");
  assert.equal(store.ledger.shouldAnimate("turn:key-1"), false);
});

test("a receipt carrying the answer (instant reply or replay) shows it without starting a run", async () => {
  const server = fake({ count: 10 });
  const { store } = build(server);
  await store.refresh();
  store.applyReceipt(receipt({ task: null, assistant_message: message(12, { id: "a12", task_id: null }) }));
  assert.deepEqual(ids(store).slice(-2), [11, 12]);
  assert.equal(store.getState().tracked, null);
  assert.equal(store.getState().announcement, "Message sent.");
});

function runningTurn() {
  const server = fake({ count: 10, active: "t12" });
  server.messages.push(message(11, { id: "u11" }));
  return server;
}

test("handoff: the live run stays until its activity is complete AND its saved answer is in; then one commit swaps them", async () => {
  const server = runningTurn();
  const { store, clock } = build(server, { savedDelays: [0, 400, 800] });
  await store.refresh();
  const frames = [];
  store.subscribe(() => frames.push(store.phase("t12")));
  assert.deepEqual(store.phase("t12"), { taskId: "t12", show: true, savedReady: false });
  store.runFinished("t12", [{ id: "e" }], "COMPLETED");
  await flush();
  assert.deepEqual(store.phase(null), { taskId: "t12", show: true, savedReady: false }, "events complete, answer not saved yet: visibly finishing");
  assert.equal(store.getState().savedWait.failed, false);
  assert.equal(store.getState().terminal.get("t12").entries.length, 1, "the activity is kept for the saved message");
  server.active = null;
  server.add(message(12, { id: "a12", task_id: "t12" }));
  await clock.advance(400);
  const done = store.phase(null);
  assert.deepEqual(done, { taskId: "t12", show: false, savedReady: true });
  assert.equal(store.getState().savedWait, null);
  assert.equal(store.getState().announcement, "Reply ready.");
  // Never a frame with neither the live run nor the saved answer.
  for (const frame of frames) assert.equal(frame.show || frame.savedReady, true, "no blank frame");
});

test("handoff: a saved answer that is slow to appear is retried with backoff until it does", async () => {
  const server = runningTurn();
  const harness = build(server, { savedDelays: [0, 400, 800, 1600] });
  const { store, clock } = harness;
  await store.refresh();
  store.runFinished("t12", [], "COMPLETED");
  await flush();
  assert.equal(server.calls.session, 2, "the first read found nothing");
  await clock.advance(400);
  assert.equal(server.calls.session, 3);
  server.add(message(12, { id: "a12", task_id: "t12" }));
  await clock.advance(800);
  assert.equal(store.phase(null).show, false);
  assert.equal(store.getState().savedWait, null);
});

test("handoff: running out of retries is shown with a way to retry, never silently declared done", async () => {
  const server = runningTurn();
  const { store, clock } = build(server, { savedDelays: [0, 100, 200] });
  await store.refresh();
  server.failSession = 99;
  store.runFinished("t12", [], "COMPLETED");
  await flush();
  await clock.advance(1000);
  const wait = store.getState().savedWait;
  assert.equal(wait.failed, true);
  assert.equal(wait.error, "session down");
  assert.equal(store.phase(null).show, true, "the turn stays visibly finishing");
  server.failSession = 0;
  server.add(message(12, { id: "a12", task_id: "t12" }));
  store.retrySaved();
  await flush(6);
  assert.equal(store.phase(null).show, false);
  assert.equal(store.getState().savedWait, null);
});

test("handoff: a refresh that finds the saved answer first ends the wait", async () => {
  const server = runningTurn();
  const { store, clock } = build(server, { savedDelays: [0, 5000, 5000] });
  await store.refresh();
  store.runFinished("t12", [], "COMPLETED");
  await flush();
  assert.notEqual(store.getState().savedWait, null);
  server.active = null;
  server.add(message(12, { id: "a12", task_id: "t12" }));
  await store.refresh();
  assert.equal(store.getState().savedWait, null);
  assert.equal(store.phase(null).show, false);
  const calls = server.calls.session;
  await clock.advance(20_000);
  assert.equal(server.calls.session, calls, "the wait stopped polling");
});

test("handoff: the saved answer already in the records when the activity completes swaps immediately", async () => {
  const server = runningTurn();
  server.add(message(12, { id: "a12", task_id: "t12" }));
  const { store } = build(server);
  await store.refresh();
  assert.deepEqual(store.phase("t12"), { taskId: "t12", show: true, savedReady: true }, "the live activity is kept until it is complete");
  store.runFinished("t12", [], "COMPLETED");
  assert.deepEqual(store.phase(null), { taskId: "t12", show: false, savedReady: true });
  assert.equal(server.calls.session, 1, "nothing more to fetch");
});

test("a run the conversation stopped reporting is followed until its answer takes over, but not forever", async () => {
  const server = runningTurn();
  const { store, clock } = build(server);
  await store.refresh();
  server.active = null;
  server.add(message(12, { id: "a12", task_id: "t12" }));
  await store.refresh();
  assert.equal(store.phase(null).show, true, "the live view is not cut off before its activity completes");
  await clock.advance(TURN_GRACE_MS - 1);
  assert.equal(store.phase(null).show, true);
  await clock.advance(2);
  assert.equal(store.phase(null).show, false, "after the grace period the saved message shows as it is");
});

test("the grace timer is cleared when the run completes normally, and on stop", async () => {
  const server = runningTurn();
  const { store, clock } = build(server);
  await store.refresh();
  server.active = null;
  server.add(message(12, { id: "a12", task_id: "t12" }));
  await store.refresh();
  assert.equal(clock.size, 1);
  store.runFinished("t12", [], "COMPLETED");
  assert.equal(clock.size, 0);
  const other = build(runningTurn());
  await other.store.refresh();
  other.store.observeSession({ ...other.store.getState(), active_task_id: null });
  assert.equal(other.clock.size, 1);
  other.store.stop();
  assert.equal(other.clock.size, 0);
});

test("a reply that streamed live hands over to its saved message without an entrance", async () => {
  const server = runningTurn();
  const { store } = build(server);
  await store.refresh();
  store.replyStreamed("t12");
  server.add(message(12, { id: "a12", task_id: "t12" }));
  await store.refresh();
  assert.equal(store.ledger.shouldAnimate("a12", "t12"), false);
  assert.equal(store.ledger.shouldAnimate("a12", null), true, "it would animate if it had not streamed");
});

test("observing the conversation's active task tracks it for the live view", async () => {
  const server = fake({ count: 4 });
  const { store } = build(server);
  await store.refresh();
  assert.equal(store.phase(null).taskId, null);
  store.observeSession({ ...server.session(), active_task_id: "t9" });
  assert.equal(store.getState().tracked, "t9");
  assert.equal(store.phase("t9").show, true);
});
