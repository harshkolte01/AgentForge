import { test } from "node:test";
import assert from "node:assert/strict";
import { createReasoningEffortStore } from "../lib/reasoning-effort-store.ts";

const ALL = ["none", "low", "medium", "high", "xhigh", "max"];
function server({ effort = "high", efforts = ALL, failReads = 0, failSaves = 0 } = {}) {
  const calls = { reads: 0, saves: [] };
  const choices = () => ({ model: "m", efforts, effort: efforts.length ? effort : null, default: efforts.length ? "high" : null });
  return {
    calls,
    fetch: async () => { calls.reads++; if (failReads-- > 0) throw new Error("down"); return choices(); },
    save: async (next) => { calls.saves.push(next); if (failSaves-- > 0) throw new Error("down"); effort = next; return choices(); },
  };
}
const settle = () => new Promise((resolve) => setImmediate(resolve));

test("the choices are read once and shared, however many composers ask", async () => {
  const fake = server({ effort: "low" });
  const store = createReasoningEffortStore(fake);
  store.load(); store.load();
  assert.deepEqual(store.getSnapshot(), { effort: null, efforts: [], status: "loading" });
  await settle();
  store.load();
  assert.deepEqual(store.getSnapshot(), { effort: "low", efforts: ALL, status: "ready" });
  assert.equal(fake.calls.reads, 1);
});

test("a model with no choice leaves nothing to show", async () => {
  const store = createReasoningEffortStore(server({ efforts: [] }));
  store.load();
  await settle();
  assert.deepEqual(store.getSnapshot(), { effort: null, efforts: [], status: "ready" });
});

test("a failed read offers nothing and is retried by the next load", async () => {
  const store = createReasoningEffortStore(server({ failReads: 1 }));
  store.load();
  await settle();
  assert.deepEqual(store.getSnapshot(), { effort: null, efforts: [], status: "error" });
  store.load();
  await settle();
  assert.equal(store.getSnapshot().status, "ready");
  assert.equal(store.getSnapshot().effort, "high");
});

test("a change shows at once; a failed save puts the previous effort back and rejects", async () => {
  const store = createReasoningEffortStore(server({ failSaves: 1 }));
  store.load();
  await settle();
  const pending = store.choose("max");
  assert.deepEqual(store.getSnapshot(), { effort: "max", efforts: ALL, status: "saving" });
  await assert.rejects(pending);
  assert.deepEqual(store.getSnapshot(), { effort: "high", efforts: ALL, status: "ready" });
  await store.choose("low");
  assert.deepEqual(store.getSnapshot(), { effort: "low", efforts: ALL, status: "ready" });
});

test("only the latest choice wins when two are made quickly", async () => {
  const fake = server();
  const store = createReasoningEffortStore(fake);
  store.load();
  await settle();
  const first = store.choose("low"), second = store.choose("xhigh");
  await Promise.all([first, second]);
  assert.equal(store.getSnapshot().effort, fake.calls.saves.at(-1));
  assert.equal(store.getSnapshot().status, "ready");
});

test("reset forgets the account's choice, and a read in flight does not bring it back", async () => {
  const store = createReasoningEffortStore(server());
  store.load();
  store.reset();
  await settle();
  assert.deepEqual(store.getSnapshot(), { effort: null, efforts: [], status: "idle" });
});
