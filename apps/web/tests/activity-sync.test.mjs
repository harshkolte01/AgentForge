import assert from "node:assert/strict";
import { test } from "node:test";
import { loadActivityHistory } from "../lib/activity/sync.ts";
import { selectContiguous, selectEntries, selectHasRunning, selectHighest, selectMissingRanges, selectActionCount } from "../lib/activity/reducer.ts";
import { builder } from "./fixtures/streams.mjs";
import { fakeServer, makeSync } from "./helpers/sync-harness.mjs";

/** A task log of `calls` finished web searches (two events each), after a task.created. */
function runLog(calls, { finish = false } = {}) {
  const b = builder({ style: "modern" });
  b.add("task.created");
  for (let i = 0; i < calls; i++) b.call("web_search", `query ${i}`);
  if (finish) b.add("task.finished", { status: "COMPLETED", error_code: null });
  return b.events;
}
const sequences = (events) => events.map((event) => event.sequence);

// Basics ------------------------------------------------------------------------------------------------------------

test("starting reads the status, catches up from the start, opens the stream and settles as live", async () => {
  const server = fakeServer();
  server.append(runLog(10));
  const { controller, clock, streams, snap } = makeSync({ server });
  assert.equal(snap().phase, "connecting");
  controller.start();
  await clock.advance(0);
  assert.equal(snap().phase, "live");
  assert.equal(selectContiguous(snap().activity), 21);
  assert.deepEqual(server.calls.history[0], [0, 100]);
  assert.equal(streams.connections.length, 1);
  assert.equal(snap().task.status, "RUNNING");
  assert.equal(snap().loading, false);
  assert.equal(snap().error, null);
  controller.stop();
});

test("while the task runs it polls every 3 seconds, and only reads history when the watermark moved", async () => {
  const server = fakeServer();
  server.append(runLog(2));
  const { controller, clock, snap } = makeSync({ server, withStream: false });
  controller.start();
  await clock.advance(0);
  assert.equal(server.calls.task, 1);
  assert.equal(server.calls.history.length, 1);
  await clock.advance(3000);
  await clock.advance(3000);
  assert.equal(server.calls.task, 3);
  assert.equal(server.calls.history.length, 1, "nothing new on the server, so no history read");
  server.append(runLog(3).slice(5).map((event) => ({ ...event, sequence: event.sequence }))); // more events appear (sequences 6..)
  await clock.advance(3000);
  assert.equal(server.calls.history.length, 2);
  assert.deepEqual(server.calls.history[1], [5, 100]);
  assert.equal(selectContiguous(snap().activity), server.watermark());
  controller.stop();
});

test("subscribers hear about changes and getSnapshot is stable in between", async () => {
  const server = fakeServer();
  server.append(runLog(1));
  const { controller, clock, history } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  const settled = controller.getSnapshot();
  assert.equal(controller.getSnapshot(), settled);
  assert.ok(history.length > 0);
  controller.start(); // idempotent
  await clock.advance(0);
  assert.equal(server.calls.task, 1);
  controller.stop();
});

// Overlap, gaps ----------------------------------------------------------------------------------------------------

test("overlapping stream and history delivery is harmless", async () => {
  const server = fakeServer();
  const log = runLog(10);
  server.append(log.slice(0, 8));
  const { controller, clock, streams, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  assert.equal(streams.connections[0].after, 8, "the stream opens from what history already gave");
  streams.open();
  server.append(log);
  for (const event of log.slice(4, 16)) streams.event(event); // the stream repeats 5..8 and adds 9..16
  await clock.advance(3000);                                  // the poll reads history from the contiguous point
  for (const event of log.slice(10)) streams.event(event);   // and the stream repeats some of that too
  assert.equal(selectContiguous(snap().activity), log.length);
  assert.equal(snap().activity.events.length, log.filter((event) => ["tool.started", "tool.finished"].includes(event.event_type)).length);
  assert.equal(selectActionCount(snap().activity), 10);
  controller.stop();
});

test("a gap in the stream is filled from history, and later events are kept", async () => {
  const server = fakeServer();
  const log = runLog(6);
  server.append(log);
  const { controller, clock, streams, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  assert.equal(selectContiguous(snap().activity), 13);

  // New events 14..17 exist on the server; the stream only delivers 15 and 17 (14 and 16 are lost).
  const more = builder({ style: "modern", start: 13 });
  more.call("web_search", "late a"); more.call("web_search", "late b");
  server.append(more.events);
  streams.open();
  streams.event(more.events[1]); streams.event(more.events[3]);
  assert.equal(selectHighest(snap().activity), 17);
  assert.equal(selectContiguous(snap().activity), 13);
  assert.deepEqual(selectMissingRanges(snap().activity), [[14, 14], [16, 16]]);
  assert.ok(snap().activity.view.length > 0);
  await clock.advance(300); // the debounce after the stream saw a gap
  assert.equal(selectContiguous(snap().activity), 17);
  assert.deepEqual(selectMissingRanges(snap().activity), []);
  assert.deepEqual(server.calls.history.at(-1), [13, 100], "recovery starts after the contiguous point, not after the largest sequence seen");
  assert.equal(selectActionCount(snap().activity), 8);
  controller.stop();
});

test("a stale status read does not hide a hole below events already seen", async () => {
  const server = fakeServer();
  const log = runLog(4);
  server.append(log.slice(0, 5));
  const { controller, clock, streams, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  streams.open();
  streams.event(log[7]); // 8 arrives over the stream while the server's status row (read next) is behind
  server.append(log); // the log catches up before the history read
  await clock.advance(3000);
  assert.equal(selectContiguous(snap().activity), log.length);
  controller.stop();
});

test("a permanent hole in the log never spins: catch-up stops, the hole is reported, the next poll tries again", async () => {
  const server = fakeServer();
  const log = runLog(5);
  server.append(log.filter((event) => event.sequence !== 4)); // 4 never exists on the server
  const { controller, clock, snap } = makeSync({ server, withStream: false });
  controller.start();
  await clock.advance(0);
  assert.equal(snap().phase, "live");
  assert.equal(selectContiguous(snap().activity), 3);
  assert.deepEqual(selectMissingRanges(snap().activity), [[4, 4]]);
  const reads = server.calls.history.length;
  assert.ok(reads <= 2, `${reads} history reads for one poll`);
  await clock.advance(3000);
  assert.ok(server.calls.history.length <= reads + 2);
  controller.stop();
});

// Long histories ---------------------------------------------------------------------------------------------------

for (const total of [150, 1250]) {
  test(`a history of ${total} calls is paged to completion without a page-count cutoff`, async () => {
    const server = fakeServer();
    const log = runLog(total, { finish: true });
    server.append(log);
    server.finish();
    const { controller, clock, snap } = makeSync({ server });
    controller.start();
    await clock.advance(0);
    assert.equal(snap().phase, "complete");
    assert.equal(selectContiguous(snap().activity), log.length);
    assert.equal(selectActionCount(snap().activity), total);
    assert.equal(server.calls.history.length, Math.ceil(log.length / 100), "one request per page, none repeated");
    assert.deepEqual(server.calls.history.map(([after]) => after), Array.from({ length: server.calls.history.length }, (_, page) => page * 100));
    controller.stop();
  });
}

test("catch-up shows progress while it pages", async () => {
  const server = fakeServer();
  server.append(runLog(450, { finish: true }));
  server.finish();
  let release;
  server.hold.history = new Promise((resolve) => { release = resolve; });
  const { controller, clock, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  assert.equal(snap().phase, "catching_up");
  assert.equal(snap().loading, true);
  assert.equal(selectContiguous(snap().activity), 0);
  release();
  await clock.advance(0);
  assert.equal(snap().phase, "complete");
  assert.equal(snap().loading, false);
  controller.stop();
});

// Completion -------------------------------------------------------------------------------------------------------------

test("terminal status before the final events: it reads them before settling and only then completes", async () => {
  const server = fakeServer();
  const log = runLog(8, { finish: true });
  server.append(log.slice(0, 10));
  const { controller, clock, streams, snap, history } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  streams.open();
  for (const event of log.slice(10, 12)) streams.event(event);  // the stream got a little more
  // The run ends: the task row says so while most of the closing events were never delivered.
  server.append(log);
  server.finish();
  await clock.advance(3000);
  assert.equal(snap().phase, "complete");
  assert.equal(selectContiguous(snap().activity), log.length);
  assert.equal(snap().terminal, true);
  // Never "complete" before the end of the log was held.
  for (const state of history) if (state.phase === "complete") assert.equal(selectContiguous(state.activity), log.length);
  assert.equal(streams.connections.every((connection) => connection.closed), true);
  assert.equal(clock.size, 0, "no timers outlive a completed run");
  controller.stop();
});

test("a call that never reported back is shown as unknown once the run is complete", async () => {
  const server = fakeServer();
  const b = builder({ style: "modern" });
  b.start("web_search", "q");
  b.add("task.finished", { status: "FAILED", error_code: "WORKER_LOST" });
  server.append(b.events);
  server.finish("FAILED");
  const { controller, clock, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  assert.equal(snap().phase, "complete");
  assert.equal(selectHasRunning(snap().activity), false);
  assert.equal(selectEntries(snap().activity)[0].outcome, "unknown");
  controller.stop();
});

test("a failed final fetch keeps the run retryable with capped exponential backoff and never completes early", async () => {
  const server = fakeServer();
  const log = runLog(5, { finish: true });
  server.append(log);
  server.finish();
  server.failNext.history = 8;
  const { controller, clock, snap, history } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  assert.equal(snap().phase, "retrying");
  assert.equal(snap().error.kind, "network");
  assert.equal(snap().attempts, 1);
  assert.equal(snap().retryAt, clock.now() + 1000);
  const delays = [];
  for (let attempt = 2; attempt <= 8; attempt++) {
    const before = clock.now();
    await clock.advance(snap().retryAt - before);
    assert.equal(snap().attempts, attempt);
    assert.notEqual(snap().phase, "complete");
    delays.push(snap().retryAt - clock.now());
  }
  assert.deepEqual(delays, [2000, 4000, 8000, 16000, 30000, 30000, 30000], "doubles, then holds at the cap");
  await clock.advance(30000); // the ninth read succeeds
  assert.equal(snap().phase, "complete");
  assert.equal(snap().attempts, 0);
  assert.equal(snap().error, null);
  for (const state of history) if (state.phase === "complete") assert.equal(selectContiguous(state.activity), log.length);
  controller.stop();
});

test("a failed status read is 'unreachable' and recovers by itself", async () => {
  const server = fakeServer();
  server.append(runLog(3));
  server.failNext.task = 2;
  const { controller, clock, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  assert.equal(snap().phase, "unreachable");
  assert.equal(snap().task, null);
  assert.equal(snap().halted, false);
  await clock.advance(1000);
  assert.equal(snap().phase, "unreachable");
  await clock.advance(2000);
  assert.equal(snap().phase, "live");
  assert.equal(snap().error, null);
  controller.stop();
});

test("a malformed answer from the API is a failed read, not a crash", async () => {
  const server = fakeServer();
  server.append(runLog(2, { finish: true }));
  server.finish();
  let calls = 0;
  const original = server.fetchHistory;
  server.fetchHistory = (...args) => (calls++ === 0 ? Promise.resolve(null) : original(...args));
  const { controller, clock, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  assert.deepEqual([snap().phase, snap().error.kind], ["retrying", "network"]);
  await clock.advance(1000);
  assert.equal(snap().phase, "complete");
  controller.stop();
});

test("retryNow skips the wait", async () => {
  const server = fakeServer();
  server.append(runLog(3, { finish: true }));
  server.finish();
  server.failNext.history = 1;
  const { controller, clock, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  assert.equal(snap().phase, "retrying");
  controller.retryNow();
  await clock.advance(0);
  assert.equal(snap().phase, "complete");
  controller.retryNow(); // harmless once complete
  controller.stop();
});

test("a log that stays short of the terminal watermark is incomplete and never completes", async () => {
  const server = fakeServer();
  const log = runLog(4, { finish: true });
  server.append(log.filter((event) => event.sequence !== 5)); // sequence 5 is missing for good
  server.finish();
  const { controller, clock, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  assert.equal(snap().phase, "retrying");
  assert.equal(snap().error.kind, "incomplete");
  await clock.advance(5 * 60_000);
  assert.notEqual(snap().phase, "complete");
  server.append(log.filter((event) => event.sequence === 5)); // it appears
  await clock.advance(30_000);
  assert.equal(snap().phase, "complete");
  controller.stop();
});

test("a delayed saved answer holds completion open and is re-checked with growing pauses", async () => {
  const server = fakeServer();
  server.append(runLog(3, { finish: true }));
  server.finish();
  let ready = false;
  server.answerReady = () => ready;
  const { controller, clock, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  assert.equal(snap().phase, "catching_up");
  assert.equal(snap().awaitingAnswer, true);
  assert.equal(snap().terminal, true);
  assert.equal(selectContiguous(snap().activity), 8);
  const waits = [];
  for (let check = 0; check < 4; check++) { waits.push(snap().retryAt - clock.now()); await clock.advance(snap().retryAt - clock.now()); assert.equal(snap().awaitingAnswer, true); }
  assert.deepEqual(waits, [500, 1000, 2000, 4000]);
  assert.notEqual(snap().phase, "complete");
  ready = true;
  await clock.advance(snap().retryAt - clock.now());
  assert.equal(snap().phase, "complete");
  assert.equal(snap().awaitingAnswer, false);
  assert.equal(server.calls.history.length, 1, "waiting for the answer does not re-read the events");
  controller.stop();
});

test("a failing answer check is retried like any other failure", async () => {
  const server = fakeServer();
  server.append(runLog(2, { finish: true }));
  server.finish();
  server.failNext.answer = 1;
  const { controller, clock, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  assert.equal(snap().phase, "retrying");
  await clock.advance(1000);
  assert.equal(snap().phase, "complete");
  controller.stop();
});

test("with no saved-answer check, a fully read terminal run completes at once", async () => {
  const server = fakeServer();
  server.append(runLog(2, { finish: true }));
  server.finish("CANCELLED");
  const { controller, clock, snap } = makeSync({ server, savedAnswerReady: undefined });
  controller.start();
  await clock.advance(0);
  assert.equal(snap().phase, "complete");
  controller.stop();
});

test("the terminal event on the stream triggers a status read soon", async () => {
  const server = fakeServer();
  const log = runLog(3, { finish: true });
  server.append(log.slice(0, -1));
  const { controller, clock, streams, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  streams.open();
  assert.equal(snap().phase, "live");
  server.append(log.slice(-1));
  server.finish();
  streams.event(log.at(-1));
  assert.equal(snap().activity.terminal.status, "COMPLETED");
  await clock.advance(300);
  assert.equal(snap().phase, "complete");
  controller.stop();
});

// Stream, visibility, revocation ---------------------------------------------------------------------------------------

test("a background tab reads what it missed the moment it returns", async () => {
  const server = fakeServer();
  server.append(runLog(2));
  const { controller, clock, visible, snap } = makeSync({ server, withStream: false });
  controller.start();
  await clock.advance(0);
  server.append(runLog(5).slice(5));
  await clock.advance(1000); // the poll is 2 seconds away
  assert.equal(selectContiguous(snap().activity), 5);
  visible.show();
  await clock.advance(0);
  assert.equal(selectContiguous(snap().activity), server.watermark());
  controller.stop();
  assert.equal(visible.listeners.size, 0, "the visibility subscription is released");
});

test("a refused or closed stream is reopened after a pause, from the contiguous point", async () => {
  const server = fakeServer();
  server.append(runLog(3));
  const { controller, clock, streams, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  streams.open();
  assert.equal(snap().streaming, true);
  streams.disconnect();
  assert.equal(snap().streaming, false);
  assert.equal(streams.connections.length, 1);
  await clock.advance(1999);
  assert.equal(streams.connections.length, 1);
  await clock.advance(1);
  assert.equal(streams.connections.length, 2);
  assert.equal(streams.connections[1].after, 7);
  controller.stop();
  assert.equal(streams.connections.every((connection) => connection.closed), true);
});

test("revocation over the stream halts everything and is surfaced", async () => {
  const server = fakeServer();
  server.append(runLog(3));
  const { controller, clock, streams, snap, lost } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  streams.open();
  streams.sessionClosed();
  assert.equal(snap().halted, true);
  assert.equal(snap().phase, "unreachable");
  assert.equal(snap().error.kind, "revoked");
  assert.deepEqual(lost, ["revoked"]);
  assert.equal(clock.size, 0);
  const reads = server.calls.task;
  await clock.advance(60_000);
  assert.equal(server.calls.task, reads, "nothing is read after revocation");
  controller.retryNow();
  await clock.advance(0);
  assert.equal(server.calls.task, reads);
  controller.stop();
});

test("a 401 halts as unauthorized; a 404 halts as not found; neither is retried", async () => {
  for (const [status, kind, lostReasons] of [[401, "unauthorized", ["unauthorized"]], [404, "not_found", []]]) {
    const server = fakeServer();
    server.append(runLog(3));
    server.failNext.task = 5;
    server.failStatus = status;
    const { controller, clock, snap, lost } = makeSync({ server });
    controller.start();
    await clock.advance(0);
    assert.equal(snap().halted, true, String(status));
    assert.equal(snap().error.kind, kind);
    assert.deepEqual(lost, lostReasons);
    await clock.advance(120_000);
    assert.equal(server.calls.task, 1);
    controller.stop();
  }
});

test("a 401 during catch-up halts too", async () => {
  const server = fakeServer();
  server.append(runLog(3));
  server.failNext.history = 3;
  server.failStatus = 401;
  const { controller, clock, snap, lost } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  assert.deepEqual([snap().halted, snap().error.kind, lost], [true, "unauthorized", ["unauthorized"]]);
  controller.stop();
});

test("stop aborts requests in flight, closes the stream and leaves no timers", async () => {
  const server = fakeServer();
  server.append(runLog(3));
  const signals = [];
  const original = server.fetchHistory;
  server.fetchHistory = (after, limit, requestSignal) => { signals.push(requestSignal); return original(after, limit, requestSignal); };
  const { controller, clock, streams, visible, snap } = makeSync({ server });
  let notified = 0;
  controller.start();
  controller.subscribe(() => notified++);
  await clock.advance(0);
  streams.open();
  server.append(runLog(6).slice(7));            // more to read on the next poll...
  server.hold.history = new Promise(() => {});  // ...which never answers
  await clock.advance(3000);
  assert.equal(snap().loading, true);
  const inFlight = signals.at(-1);
  assert.equal(inFlight.aborted, false);
  controller.stop();
  assert.equal(inFlight.aborted, true);
  assert.equal(streams.connections.length, 1);
  assert.equal(streams.connections[0].closed, true);
  assert.equal(clock.size, 0);
  assert.equal(visible.listeners.size, 0);
  const before = notified;
  streams.connections[0].handlers.onEvent(runLog(1)[1]);
  await clock.advance(60_000);
  assert.equal(notified, before, "no change is published after stop");
  assert.equal(server.calls.task, 2);
  controller.stop(); // twice is fine
});

// Live text --------------------------------------------------------------------------------------------------------------

const delta = (extra) => ({ task_id: "task-1", epoch: 1, revision: 0, kind: "progress", offset: 0, text: "", reset: false, ...extra });

test("live text arrives as deltas, ignores stale and duplicate frames, and records progress", async () => {
  const server = fakeServer();
  server.append(runLog(1));
  const { controller, clock, streams, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  streams.open();
  streams.delta(delta({ text: "Hello", reset: true }));
  streams.delta(delta({ offset: 5, text: ", wor" }));
  streams.delta(delta({ offset: 5, text: ", wor" }));       // duplicate
  streams.delta(delta({ offset: 10, text: "ld" }));
  streams.delta(delta({ epoch: 0, offset: 0, text: "old", reset: true })); // stale
  streams.delta({ ...delta({ offset: 0, text: "other" }), task_id: "someone-else" });
  assert.equal(snap().live.text, "Hello, world");
  assert.equal(snap().live.epoch, 1);
  assert.equal(snap().lastProgressAt, clock.now());
  streams.delta(delta({ revision: 1, text: "", reset: true })); // cleared
  assert.equal(snap().live.text, "");
  controller.stop();
});

test("a delta that cannot be applied fetches the snapshot", async () => {
  const server = fakeServer();
  server.append(runLog(1));
  const { controller, clock, streams, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  streams.open();
  const before = server.calls.snapshot;
  server.live = { ...server.live, epoch: 2, revision: 1, kind: "answer", text: "The whole answer so far" };
  streams.delta(delta({ epoch: 2, revision: 1, offset: 40, text: "tail" })); // frames before it were missed
  assert.equal(snap().live.text, "");
  await clock.advance(0);
  assert.equal(server.calls.snapshot, before + 1);
  assert.deepEqual([snap().live.text, snap().live.kind, snap().live.epoch], ["The whole answer so far", "answer", 2]);
  // The stream keeps appending from there.
  streams.delta(delta({ epoch: 2, revision: 1, kind: "answer", offset: 23, text: " and more" }));
  assert.equal(snap().live.text, "The whole answer so far and more");
  controller.stop();
});

test("without a stream the snapshot is the polling fallback for live text", async () => {
  const server = fakeServer();
  server.append(runLog(1));
  const { controller, clock, snap } = makeSync({ server, withStream: false });
  controller.start();
  await clock.advance(0);
  server.live = { ...server.live, epoch: 1, text: "typing" };
  await clock.advance(3000);
  assert.equal(snap().live.text, "typing");
  server.live = { ...server.live, text: "typing on" };
  await clock.advance(3000);
  assert.equal(snap().live.text, "typing on");
  controller.stop();
});

test("with a healthy stream, polling does not fetch the snapshot", async () => {
  const server = fakeServer();
  server.append(runLog(1));
  const { controller, clock, streams } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  streams.open();
  const before = server.calls.snapshot;
  await clock.advance(9000);
  assert.equal(server.calls.snapshot, before);
  controller.stop();
});

// Historical loading ---------------------------------------------------------------------------------------------------

test("loadActivityHistory pages until the server has no more, however many pages that is", async () => {
  const log = runLog(1500);
  const requests = [];
  const fetchHistory = async (after, limit) => { requests.push([after, limit]); return log.filter((event) => event.sequence > after).slice(0, limit); };
  const pages = [];
  const events = await loadActivityHistory(fetchHistory, { onPage: (page) => pages.push(page.length) });
  assert.equal(events.length, log.length);
  assert.deepEqual(sequences(events), sequences(log));
  assert.equal(requests.length, Math.ceil(log.length / 100));
  assert.equal(pages.length, requests.length);
  assert.deepEqual(requests[1], [100, 100]);
});

test("loadActivityHistory stops on an empty page, an exact multiple, or a server that ignores the cursor", async () => {
  const log = runLog(100); // 201 events: 2 full pages and a short one
  let calls = 0;
  assert.equal((await loadActivityHistory(async (after, limit) => { calls++; return log.filter((event) => event.sequence > after).slice(0, limit); }, { pageSize: 67 })).length, 201);
  assert.equal(calls, 4);
  calls = 0;
  const exact = runLog(99).slice(0, 100); // exactly one full page, then an empty one
  assert.equal((await loadActivityHistory(async (after, limit) => { calls++; return exact.filter((event) => event.sequence > after).slice(0, limit); })).length, 100);
  assert.equal(calls, 2);
  const stubborn = runLog(5);
  calls = 0;
  const events = await loadActivityHistory(async () => { calls++; return stubborn; }, { pageSize: 2 });
  assert.equal(calls, 2, "a page that moves the cursor nowhere ends the load");
  assert.ok(events.length >= stubborn.length);
});

test("loadActivityHistory honours abort and starts after a cursor", async () => {
  const log = runLog(300);
  const controller = new AbortController();
  let calls = 0;
  await assert.rejects(loadActivityHistory(async (after, limit) => { calls++; if (calls === 2) controller.abort(); return log.filter((event) => event.sequence > after).slice(0, limit); }, { signal: controller.signal }), { name: "AbortError" });
  assert.equal(calls, 2);
  const rest = await loadActivityHistory(async (after, limit) => log.filter((event) => event.sequence > after).slice(0, limit), { after: 550 });
  assert.equal(rest.length, log.length - 550);
  await assert.rejects(loadActivityHistory(async () => { throw new Error("network"); }), /network/);
});
