import assert from "node:assert/strict";
import { test } from "node:test";
import { FRAME_FALLBACK_MS, createFrameBatcher } from "../features/sessions/frame-batcher.ts";

/** A manual frame loop and timer queue. */
function env() {
  let id = 0;
  const frames = new Map();
  const timers = new Map();
  return {
    api: {
      requestFrame: (callback) => { frames.set(++id, callback); return id; },
      cancelFrame: (handle) => { frames.delete(handle); },
      setTimeout: (callback, ms) => { timers.set(++id, { callback, ms }); return id; },
      clearTimeout: (handle) => { timers.delete(handle); },
    },
    frame() { const due = [...frames.values()]; frames.clear(); due.forEach((callback) => callback()); },
    fire() { const due = [...timers.values()]; timers.clear(); due.forEach(({ callback }) => callback()); },
    get frames() { return frames.size; },
    get timers() { return timers.size; },
  };
}

test("any number of updates in a burst cost one flush, on the next frame", () => {
  const e = env();
  let flushed = 0;
  const batcher = createFrameBatcher(() => flushed++, e.api);
  for (let i = 0; i < 50; i++) batcher.schedule();
  assert.equal(flushed, 0, "nothing is flushed synchronously");
  assert.equal(batcher.pending, true);
  assert.equal(e.frames, 1);
  e.frame();
  assert.equal(flushed, 1);
  assert.equal(batcher.pending, false);
  batcher.schedule();
  e.frame();
  assert.equal(flushed, 2, "the next burst gets its own frame");
});

test("the backup timer flushes when frames do not run (a hidden tab), and the frame is then cancelled", () => {
  const e = env();
  let flushed = 0;
  const batcher = createFrameBatcher(() => flushed++, e.api);
  batcher.schedule();
  assert.equal(e.timers, 1);
  e.fire();
  assert.equal(flushed, 1);
  assert.equal(e.frames, 0, "the pending frame was cancelled");
  e.frame();
  assert.equal(flushed, 1, "and could not flush a second time");
});

test("whichever comes first wins, and the other is cleaned up", () => {
  const e = env();
  let flushed = 0;
  const batcher = createFrameBatcher(() => flushed++, e.api);
  batcher.schedule();
  e.frame();
  assert.equal(e.timers, 0);
  e.fire();
  assert.equal(flushed, 1);
});

test("flushNow runs a waiting update at once; with nothing waiting it does nothing", () => {
  const e = env();
  let flushed = 0;
  const batcher = createFrameBatcher(() => flushed++, e.api);
  batcher.flushNow();
  assert.equal(flushed, 0);
  batcher.schedule();
  batcher.flushNow();
  assert.equal(flushed, 1);
  assert.equal(e.frames + e.timers, 0);
});

test("cancel drops the waiting update and every handle", () => {
  const e = env();
  let flushed = 0;
  const batcher = createFrameBatcher(() => flushed++, e.api);
  batcher.schedule();
  batcher.cancel();
  e.frame(); e.fire();
  assert.equal(flushed, 0);
  assert.equal(batcher.pending, false);
  batcher.schedule();
  e.frame();
  assert.equal(flushed, 1, "usable again after a cancel");
});

test("an update scheduled from inside the flush gets the next frame", () => {
  const e = env();
  let flushed = 0;
  let batcher;
  batcher = createFrameBatcher(() => { if (flushed++ === 0) batcher.schedule(); }, e.api);
  batcher.schedule();
  e.frame();
  assert.equal(flushed, 1);
  assert.equal(batcher.pending, true);
  e.frame();
  assert.equal(flushed, 2);
});

test("the default backup is a few frames, not a typing delay", () => {
  assert.ok(FRAME_FALLBACK_MS >= 100 && FRAME_FALLBACK_MS <= 500);
});
