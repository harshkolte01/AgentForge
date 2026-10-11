import { test } from "node:test";
import assert from "node:assert/strict";
import { createApprovalModeStore } from "../lib/approval-mode-store.ts";

function server({ mode = "ask", failReads = 0, failSaves = 0 } = {}) {
  const calls = { reads: 0, saves: [] };
  return {
    calls,
    fetch: async () => { calls.reads++; if (failReads-- > 0) throw new Error("down"); return { mode }; },
    save: async (next) => { calls.saves.push(next); if (failSaves-- > 0) throw new Error("down"); mode = next; return { mode }; },
  };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));

test("the mode is read once and shared, however many controls ask", async () => {
  const fake = server({ mode: "auto" });
  const store = createApprovalModeStore(fake);
  store.load(); store.load();
  assert.deepEqual(store.getSnapshot(), { mode: null, status: "loading" });
  await settle();
  store.load();
  assert.deepEqual(store.getSnapshot(), { mode: "auto", status: "ready" });
  assert.equal(fake.calls.reads, 1);
});

test("a failed read falls back to ask, stays usable and is retried by the next load", async () => {
  const fake = server({ mode: "auto", failReads: 1 });
  const store = createApprovalModeStore(fake);
  store.load();
  await settle();
  assert.deepEqual(store.getSnapshot(), { mode: "ask", status: "error" });
  store.load();
  await settle();
  assert.deepEqual(store.getSnapshot(), { mode: "auto", status: "ready" });
});

test("a change shows at once; a failed save puts the previous mode back and rejects", async () => {
  const fake = server({ failSaves: 1 });
  const store = createApprovalModeStore(fake);
  store.load();
  await settle();
  const seen = [];
  store.subscribe(() => seen.push(store.getSnapshot().mode));
  await assert.rejects(store.choose("auto"));
  assert.deepEqual(seen, ["auto", "ask"]);
  await store.choose("auto");
  assert.deepEqual(store.getSnapshot(), { mode: "auto", status: "ready" });
});

test("a read that lands after a newer choice does not undo it", async () => {
  let release;
  const store = createApprovalModeStore({ fetch: () => new Promise((resolve) => { release = () => resolve({ mode: "ask" }); }), save: async (mode) => ({ mode }) });
  store.load();
  await store.choose("auto");
  release();
  await settle();
  assert.equal(store.getSnapshot().mode, "auto");
  store.reset();
  assert.deepEqual(store.getSnapshot(), { mode: null, status: "idle" });
});
