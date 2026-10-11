import { test } from "node:test";
import assert from "node:assert/strict";
import { createDeferredMerge, secondsLeft } from "../lib/deferred-merge.ts";

/** A clock the test moves by hand, so "five seconds" takes no time. */
function manualClock() {
  let now = 1000;
  const timers = new Map();
  let next = 1;
  return {
    now: () => now,
    setTimeout: (run, ms) => { const id = next++; timers.set(id, { at: now + ms, run }); return id; },
    clearTimeout: (id) => { timers.delete(id); },
    advance(ms) {
      now += ms;
      for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.run(); }
    },
    get timers() { return timers.size; },
  };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));
const url = "https://github.com/o/r/pull/12";

function setup(options = {}) {
  const clock = manualClock();
  const store = createDeferredMerge(clock, 5000);
  const calls = [];
  const start = (key = url, overrides = {}) => store.start(key, { label: "Squash and merge #12 into main", run: async () => { calls.push(key); if (options.fail) throw new Error("boom"); }, failure: (error) => `failed: ${error.message}`, success: "Merged #12", ...overrides });
  return { clock, store, calls, start };
}

test("the merge runs once, only after the whole undo window", async () => {
  const { clock, store, calls, start } = setup();
  assert.equal(start(), true);
  assert.deepEqual(store.getSnapshot().pending.map((item) => [item.key, item.phase, item.endsAt]), [[url, "waiting", 6000]]);
  clock.advance(4999);
  assert.deepEqual(calls, []);
  clock.advance(1);
  assert.deepEqual(calls, [url]);
  assert.equal(store.get(url).phase, "merging");
  await settle();
  assert.equal(store.get(url), undefined);
  assert.deepEqual(store.getSnapshot().notices.map((notice) => [notice.tone, notice.text]), [["success", "Merged #12"]]);
  clock.advance(60_000);
  assert.deepEqual(calls, [url]);
});

test("undoing inside the window means it never runs", async () => {
  const { clock, store, calls, start } = setup();
  start();
  clock.advance(3000);
  assert.equal(store.cancel(url), true);
  assert.equal(store.get(url), undefined);
  assert.equal(clock.timers, 0);
  clock.advance(10_000);
  await settle();
  assert.deepEqual(calls, []);
  assert.deepEqual(store.getSnapshot().notices, []);
});

test("once the merge is under way it can no longer be undone", async () => {
  const { clock, store, start } = setup();
  start();
  clock.advance(5000);
  assert.equal(store.cancel(url), false);
  assert.equal(store.get(url).phase, "merging");
  await settle();
});

test("a second click on the same pull request does not start a second wait", () => {
  const { clock, store, calls, start } = setup();
  assert.equal(start(), true);
  assert.equal(start(), false);
  assert.equal(store.getSnapshot().pending.length, 1);
  clock.advance(5000);
  assert.deepEqual(calls, [url]);
});

test("pull requests wait independently, keyed by URL", async () => {
  const { clock, store, calls, start } = setup();
  const other = "https://github.com/o/r/pull/13";
  start(url);
  clock.advance(2000);
  start(other);
  assert.equal(store.cancel(url), true);
  clock.advance(5000);
  await settle();
  assert.deepEqual(calls, [other]);
});

test("a failure leaves a notice that stays until it is dismissed", async () => {
  const { clock, store, start } = setup({ fail: true });
  start();
  clock.advance(5000);
  await settle();
  assert.equal(store.get(url), undefined);
  const [notice] = store.getSnapshot().notices;
  assert.deepEqual([notice.tone, notice.text], ["error", "failed: boom"]);
  clock.advance(60_000);
  assert.equal(store.getSnapshot().notices.length, 1);
  store.dismiss(notice.id);
  assert.deepEqual(store.getSnapshot().notices, []);
});

test("a run that throws at once is a failure too, and listeners hear every change", async () => {
  const { clock, store, start } = setup();
  let heard = 0;
  const stop = store.subscribe(() => { heard++; });
  start(url, { run: () => { throw new Error("sync boom"); } });
  clock.advance(5000);
  await settle();
  assert.equal(store.getSnapshot().notices[0].text, "failed: sync boom");
  assert.ok(heard >= 3);
  stop();
  const before = heard;
  store.dismiss(store.getSnapshot().notices[0].id);
  assert.equal(heard, before);
});

test("the countdown never reads above the window and never below zero", () => {
  assert.equal(secondsLeft(6000, 1000), 5);
  assert.equal(secondsLeft(6000, 999), 5);
  assert.equal(secondsLeft(6000, 3500), 3);
  assert.equal(secondsLeft(6000, 5999), 1);
  assert.equal(secondsLeft(6000, 7000), 0);
});

test("the snapshot is a new object only when something changed", () => {
  const { store, start } = setup();
  const empty = store.getSnapshot();
  assert.equal(store.getSnapshot(), empty);
  start();
  assert.notEqual(store.getSnapshot(), empty);
});
