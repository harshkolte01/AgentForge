import assert from "node:assert/strict";
import { test } from "node:test";
import { ACTIVITY_DISCLOSURE, createDisclosureStore, disclosureKey } from "../lib/activity/disclosure.ts";

const memory = (initial = {}) => {
  const data = { ...initial };
  return { data, getItem: (key) => data[key] ?? null, setItem: (key, value) => { data[key] = value; } };
};

test("an untouched disclosure has no value and follows its default; a manual choice wins", () => {
  const store = createDisclosureStore(null);
  const key = disclosureKey("task-1", ACTIVITY_DISCLOSURE);
  assert.equal(key, "task-1:activity");
  assert.equal(store.get(key), undefined);
  assert.equal(store.resolve(key, true), true);
  assert.equal(store.resolve(key, false), false);
  store.set(key, false);
  assert.equal(store.get(key), false);
  assert.equal(store.resolve(key, true), false, "a manual close beats an open default");
  store.set(key, true);
  assert.equal(store.resolve(key, false), true);
});

test("listeners hear about changes only, and can unsubscribe", () => {
  const store = createDisclosureStore(null);
  let heard = 0;
  const stop = store.subscribe(() => heard++);
  store.set("a", true);
  store.set("a", true);
  store.set("a", false);
  assert.equal(heard, 2);
  stop();
  store.set("a", true);
  assert.equal(heard, 2);
});

test("choices survive a new store over the same session storage (navigation within the page session)", () => {
  const storage = memory();
  createDisclosureStore(storage).set("task-1:tool:abc", true);
  const later = createDisclosureStore(storage);
  assert.equal(later.get("task-1:tool:abc"), true);
  assert.equal(later.get("task-1:other"), undefined);
});

test("missing, damaged or throwing storage only costs persistence", () => {
  assert.equal(createDisclosureStore(memory({ "agentforge:disclosure:v1": "{not json" })).get("x"), undefined);
  assert.equal(createDisclosureStore(memory({ "agentforge:disclosure:v1": JSON.stringify([["ok", true], ["bad"], [1, true], ["worse", "yes"]]) })).get("ok"), true);
  const hostile = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("quota"); } };
  const store = createDisclosureStore(hostile);
  store.set("k", true);
  assert.equal(store.get("k"), true, "memory still remembers for this page");
});

test("the store is bounded: the oldest choices are forgotten first", () => {
  const store = createDisclosureStore(null, { limit: 3 });
  for (const key of ["a", "b", "c", "d"]) store.set(key, true);
  assert.deepEqual(["a", "b", "c", "d"].map((key) => store.get(key)), [undefined, true, true, true]);
  store.set("b", false); // touching a key makes it the newest
  store.set("e", true);
  assert.deepEqual(["b", "c", "d", "e"].map((key) => store.get(key)), [false, undefined, true, true]);
});

test("clearing a task forgets only its choices", () => {
  const storage = memory();
  const store = createDisclosureStore(storage);
  store.set("task-1:activity", true); store.set("task-1:tool:x", false); store.set("task-2:activity", true);
  let heard = 0;
  store.subscribe(() => heard++);
  store.clear("task-1:");
  assert.deepEqual(["task-1:activity", "task-1:tool:x", "task-2:activity"].map((key) => store.get(key)), [undefined, undefined, true]);
  assert.equal(heard, 1);
  store.clear("nothing:");
  assert.equal(heard, 1);
  assert.equal(createDisclosureStore(storage).get("task-1:activity"), undefined);
  store.clear();
  assert.equal(store.get("task-2:activity"), undefined);
});
