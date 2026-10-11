import { test } from "node:test";
import assert from "node:assert/strict";
import { isReviewed, loadReviewed, saveReviewed } from "../lib/delivery-reviewed.ts";
import { hashText } from "../lib/delivery-status.ts";

function memory(seed = {}) {
  const data = new Map(Object.entries(seed));
  return { getItem: (key) => data.get(key) ?? null, setItem: (key, value) => { data.set(key, value); }, removeItem: (key) => { data.delete(key); }, data };
}

test("a mark holds only while the file's diff is the one that was reviewed", () => {
  const storage = memory();
  const before = hashText("modified\n@@ -1 +1 @@\n-a\n+b");
  const files = new Map([["src/a.ts", before]]);
  saveReviewed("s1", { "src/a.ts": before }, files, storage);
  assert.equal(isReviewed(loadReviewed("s1", storage), "src/a.ts", before), true);
  const after = hashText("modified\n@@ -1 +1 @@\n-a\n+c");
  assert.equal(isReviewed(loadReviewed("s1", storage), "src/a.ts", after), false);
});

test("marks belong to one conversation", () => {
  const storage = memory();
  saveReviewed("s1", { "a.ts": "h" }, null, storage);
  assert.deepEqual(loadReviewed("s2", storage), {});
  assert.deepEqual(loadReviewed("s1", storage), { "a.ts": "h" });
});

test("marks for files that are no longer changed are dropped when saving, and the entry goes away when none are left", () => {
  const storage = memory();
  const saved = saveReviewed("s1", { "a.ts": "h1", "gone.ts": "h2" }, new Map([["a.ts", "h1"]]), storage);
  assert.deepEqual(saved, { "a.ts": "h1" });
  saveReviewed("s1", {}, new Map(), storage);
  assert.equal(storage.data.size, 0);
});

test("missing, broken or hostile storage never throws", () => {
  assert.deepEqual(loadReviewed("s1", null), {});
  assert.deepEqual(loadReviewed("s1", memory({ "agentforge.delivery.reviewed.s1": "{not json" })), {});
  assert.deepEqual(loadReviewed("s1", memory({ "agentforge.delivery.reviewed.s1": "[1,2]" })), {});
  assert.deepEqual(loadReviewed("s1", memory({ "agentforge.delivery.reviewed.s1": JSON.stringify({ a: "ok", b: 5, c: null }) })), { a: "ok" });
  const full = { getItem: () => { throw new Error("denied"); }, setItem: () => { throw new Error("quota"); }, removeItem: () => { throw new Error("denied"); } };
  assert.deepEqual(loadReviewed("s1", full), {});
  assert.deepEqual(saveReviewed("s1", { "a.ts": "h" }, null, full), { "a.ts": "h" });
  assert.deepEqual(saveReviewed("s1", { "a.ts": "h" }, null, null), { "a.ts": "h" });
});
