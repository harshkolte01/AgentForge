import assert from "node:assert/strict";
import { test } from "node:test";
import { loadHistoryEntries } from "../features/sessions/history-activity.ts";
import { summarizeEntries } from "../features/sessions/timeline-model.ts";
import { builder } from "./fixtures/streams.mjs";

/** A fake history API over an event list: pages by `after` and `limit`, like /events/history. */
const server = (events, { failOn } = {}) => {
  const requests = [];
  const fetchHistory = async (after, limit, signal) => {
    signal?.throwIfAborted();
    requests.push([after, limit]);
    // The real history endpoint rejects larger pages with HTTP 422.
    if (limit > 100) throw new Error("HTTP 422: history limit must be <= 100");
    if (failOn && requests.length === failOn) throw new Error("boom");
    return events.filter((event) => event.sequence > after).slice(0, limit);
  };
  return { fetchHistory, requests };
};

test("every page is read, with no cutoff: 450 actions come back exactly", async () => {
  const b = builder({ style: "modern" });
  for (let i = 0; i < 450; i++) b.call("run_sandbox_command", `cmd_${i}`);
  const { fetchHistory, requests } = server(b.events);
  const entries = await loadHistoryEntries("task-1", { terminal: true }, fetchHistory);
  assert.equal(summarizeEntries(entries).actions, 450);
  assert.ok(requests.length >= 10, "900 events plus the final empty page use 100-event pages");
  assert.ok(requests.every(([, limit]) => limit <= 100), "never above the API's page limit");
});

test("note-only activity loads as notes", async () => {
  const b = builder({ style: "modern" });
  b.note("First, the plan.", 1);
  b.note("Then the answer.", 2);
  const entries = await loadHistoryEntries("task-1", { terminal: true }, server(b.events).fetchHistory);
  assert.deepEqual(summarizeEntries(entries), { actions: 0, notes: 2 });
});

test("a call that never reported back is unknown once the message says the run is over, and running otherwise", async () => {
  const b = builder({ style: "modern" });
  b.start("web_search", "q");
  const done = await loadHistoryEntries("task-1", { terminal: true }, server(b.events).fetchHistory);
  assert.equal(done[0].outcome, "unknown");
  const live = await loadHistoryEntries("task-1", { terminal: false }, server(b.events).fetchHistory);
  assert.equal(live[0].outcome, "running");
});

test("a failed page rejects instead of returning a shortened list, so the caller can offer a retry", async () => {
  const b = builder({ style: "modern" });
  for (let i = 0; i < 120; i++) b.call("web_search", `q${i}`);
  await assert.rejects(loadHistoryEntries("task-1", { terminal: true }, server(b.events, { failOn: 2 }).fetchHistory), /boom/);
});

test("an aborted load rejects with the abort", async () => {
  const b = builder({ style: "modern" });
  b.call("web_search", "q");
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(loadHistoryEntries("task-1", { terminal: true }, server(b.events).fetchHistory, controller.signal), (error) => error.name === "AbortError");
});

test("an empty history is an empty list", async () => {
  assert.deepEqual(await loadHistoryEntries("task-1", { terminal: true }, server([]).fetchHistory), []);
});
