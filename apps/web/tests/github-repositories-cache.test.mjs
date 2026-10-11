import { test } from "node:test";
import assert from "node:assert/strict";
import { createRepositoryCache } from "../lib/github-repositories-cache.ts";

const repo = (id) => ({ id, full_name: `owner/repo-${id}`, private: false, default_branch: "main", installation_id: 1, can_write: false });

function harness() {
  const clock = { now: 0 };
  const calls = [];
  const cache = createRepositoryCache({
    now: () => clock.now,
    ttlMs: 60_000,
    load: () => new Promise((resolve, reject) => calls.push({ resolve, reject })),
  });
  return { clock, calls, cache };
}

test("a fresh list is served from the cache without another read", async () => {
  const { clock, calls, cache } = harness();
  const first = cache.get();
  calls[0].resolve([repo(1)]);
  assert.deepEqual(await first, [repo(1)]);
  clock.now = 59_000;
  assert.ok(cache.fresh());
  assert.deepEqual(await cache.get(), [repo(1)]);
  assert.equal(calls.length, 1);
});

test("reads in flight are shared, and a stale list is read again", async () => {
  const { clock, calls, cache } = harness();
  const a = cache.get();
  const b = cache.get();
  assert.equal(calls.length, 1);
  calls[0].resolve([repo(1)]);
  assert.deepEqual(await a, await b);
  clock.now = 61_000;
  assert.equal(cache.fresh(), false);
  assert.deepEqual(cache.peek(), [repo(1)]);
  const c = cache.get();
  assert.equal(calls.length, 2);
  calls[1].resolve([repo(2)]);
  assert.deepEqual(await c, [repo(2)]);
});

test("failures are not cached and force or invalidate always read again", async () => {
  const { calls, cache } = harness();
  const failed = cache.get();
  calls[0].reject(new Error("unavailable"));
  await assert.rejects(failed);
  assert.equal(cache.peek(), null);
  const retried = cache.get();
  assert.equal(calls.length, 2);
  calls[1].resolve([repo(1)]);
  await retried;
  const forced = cache.get({ force: true });
  assert.equal(calls.length, 3);
  calls[2].resolve([repo(3)]);
  assert.deepEqual(await forced, [repo(3)]);
  cache.invalidate();
  assert.equal(cache.peek(), null);
  const after = cache.get();
  assert.equal(calls.length, 4);
  calls[3].resolve([repo(4)]);
  assert.deepEqual(await after, [repo(4)]);
});

test("an older read finishing after a newer one never overwrites it", async () => {
  const { calls, cache } = harness();
  const old = cache.get();
  const newer = cache.get({ force: true });
  calls[1].resolve([repo(2)]);
  await newer;
  calls[0].resolve([repo(1)]);
  await old;
  assert.deepEqual(cache.peek(), [repo(2)]);
});
