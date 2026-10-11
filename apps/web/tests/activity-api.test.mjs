import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { createBrowserSync, openEventStream } from "../lib/activity/browser.ts";
import { ApiError, fetchActivitySummary, fetchEventHistory, fetchStreamSnapshot, fetchToolDetail } from "../lib/task-api.ts";
import { loadHistoryEntries } from "../features/sessions/history-activity.ts";
import { summarizeEntries } from "../features/sessions/timeline-model.ts";
import { builder } from "./fixtures/streams.mjs";
import { flush } from "./helpers/clock.mjs";

const realFetch = globalThis.fetch;
const realWindow = globalThis.window;
const realDocument = globalThis.document;
afterEach(() => {
  globalThis.fetch = realFetch;
  if (realWindow === undefined) delete globalThis.window; else globalThis.window = realWindow;
  if (realDocument === undefined) delete globalThis.document; else globalThis.document = realDocument;
});

const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function stubFetch(handler) {
  const requests = [];
  globalThis.fetch = async (url, init) => { requests.push({ url, init }); return handler(url, init); };
  globalThis.window = { dispatchEvent: (event) => (globalThis.window.events ??= []).push(event.type) };
  return requests;
}

test("the typed helpers hit the documented paths, uncached and with the session cookie", async () => {
  const requests = stubFetch(() => json({ ok: true }));
  await fetchActivitySummary("t-1");
  await fetchToolDetail("t-1", "00000000-0000-4000-8000-000000000001");
  await fetchStreamSnapshot("t-1");
  await fetchEventHistory("t-1", 40, 100);
  assert.deepEqual(requests.map((request) => request.url), [
    "/api/v1/tasks/t-1/activity-summary",
    "/api/v1/tasks/t-1/tool-calls/00000000-0000-4000-8000-000000000001",
    "/api/v1/tasks/t-1/stream-snapshot",
    "/api/v1/tasks/t-1/events/history?after=40&limit=100",
  ]);
  for (const { init } of requests) assert.deepEqual([init.cache, init.credentials], ["no-store", "same-origin"]);
});

test("ids are encoded into the path and a signal is passed through", async () => {
  const requests = stubFetch(() => json({}));
  const controller = new AbortController();
  await fetchToolDetail("a/b", "c d", { signal: controller.signal });
  assert.equal(requests[0].url, "/api/v1/tasks/a%2Fb/tool-calls/c%20d");
  assert.equal(requests[0].init.signal, controller.signal);
});

test("an unavailable tool detail is a 404 ApiError that keeps the server's code and a friendly message", async () => {
  stubFetch(() => json({ detail: "tool_detail_unavailable" }, 404));
  await assert.rejects(fetchToolDetail("t", "c"), (error) => {
    assert.ok(error instanceof ApiError);
    assert.deepEqual([error.status, error.code, error.message], [404, "tool_detail_unavailable", "There are no details to show for this action."]);
    return true;
  });
});

test("a 401 raises the session-expired event and carries its status", async () => {
  stubFetch(() => json({ detail: "session_required" }, 401));
  await assert.rejects(fetchActivitySummary("t"), (error) => error.status === 401 && error.code === "session_required");
  assert.deepEqual(globalThis.window.events, ["agentforge-session-expired"]);
});

// The EventSource adapter -------------------------------------------------------------------------------------------------

class FakeEventSource {
  static CLOSED = 2;
  static instances = [];
  constructor(url) { this.url = url; this.listeners = {}; this.readyState = 1; this.onopen = null; this.onerror = null; this.closedByUs = false; FakeEventSource.instances.push(this); }
  addEventListener(type, listener) { (this.listeners[type] ??= []).push(listener); }
  close() { this.closedByUs = true; this.readyState = 2; }
  fire(type, data) { for (const listener of this.listeners[type] ?? []) listener({ data: typeof data === "string" ? data : JSON.stringify(data) }); }
}

function open(taskId = "t-1", after = 7) {
  FakeEventSource.instances = [];
  const seen = { events: [], deltas: [], opens: 0, closed: 0, disconnects: 0 };
  const connection = openEventStream(taskId, after, {
    onOpen: () => seen.opens++, onEvent: (event) => seen.events.push(event), onDelta: (delta) => seen.deltas.push(delta),
    onSessionClosed: () => seen.closed++, onDisconnect: () => seen.disconnects++,
  }, FakeEventSource);
  return { source: FakeEventSource.instances[0], seen, connection };
}

test("the stream opens from the contiguous point and routes durable events, live text and open", () => {
  const { source, seen } = open("t/1", 7);
  assert.equal(source.url, "/api/v1/tasks/t%2F1/events?after=7");
  source.onopen();
  source.fire("tool.started", { task_id: "t-1", sequence: 8, event_type: "tool.started", payload: { tool: "web_search" } });
  source.fire("assistant.message", { task_id: "t-1", sequence: 9, event_type: "assistant.message", payload: { text: "hi" } });
  source.fire("message.delta", { task_id: "t-1", epoch: 1, revision: 2, kind: "answer", offset: 0, text: "Hel", reset: true });
  assert.equal(seen.opens, 1);
  assert.deepEqual(seen.events.map((event) => event.sequence), [8, 9]);
  assert.deepEqual(seen.deltas, [{ task_id: "t-1", epoch: 1, revision: 2, kind: "answer", offset: 0, text: "Hel", reset: true }]);
});

test("every durable event type the log can hold is listened for", () => {
  const { source } = open();
  for (const type of ["task.created", "task.status", "tool.started", "tool.finished", "assistant.message", "subagent.started", "subagent.finished", "task.finished", "sandbox.workspace.created", "task.repair.started", "approval.required", "message.delta", "session.closed"]) {
    assert.ok(source.listeners[type]?.length, type);
  }
});

test("malformed frames are ignored", () => {
  const { source, seen } = open();
  source.fire("tool.started", "{not json");
  source.fire("tool.started", { nope: true });
  source.fire("message.delta", { task_id: "t-1" });
  source.fire("message.delta", "null");
  assert.deepEqual([seen.events.length, seen.deltas.length], [0, 0]);
});

test("session.closed closes the source and reports revocation once", () => {
  const { source, seen } = open();
  source.fire("session.closed", {});
  source.fire("session.closed", {});
  assert.equal(source.closedByUs, true);
  assert.equal(seen.closed, 1);
  source.fire("tool.started", { task_id: "t-1", sequence: 3, event_type: "tool.started", payload: {} });
  assert.equal(seen.events.length, 0, "nothing is delivered after the session closed");
});

test("a dropped connection the browser retries is not a disconnect; a refused one is, once", () => {
  const { source, seen } = open();
  source.readyState = 0; // CONNECTING: the browser is reconnecting by itself
  source.onerror();
  assert.equal(seen.disconnects, 0);
  source.readyState = 2;
  source.onerror();
  source.onerror();
  assert.equal(seen.disconnects, 1);
});

test("closing the connection silences the source", () => {
  const { source, seen, connection } = open();
  connection.close();
  assert.equal(source.closedByUs, true);
  source.onopen();
  source.fire("tool.started", { task_id: "t-1", sequence: 1, event_type: "tool.started", payload: {} });
  source.readyState = 2;
  source.onerror();
  assert.deepEqual([seen.opens, seen.events.length, seen.disconnects], [0, 0, 0]);
});

// The wired controller ------------------------------------------------------------------------------------------------------

test("createBrowserSync reads the task and its events through the typed API and completes", async () => {
  const log = [
    { task_id: "t-1", sequence: 1, event_type: "tool.started", payload: { tool: "web_search", detail: "q", tool_call_id: "c1" }, created_at: "" },
    { task_id: "t-1", sequence: 2, event_type: "tool.finished", payload: { tool: "web_search", status: "ok", outcome: "completed", tool_call_id: "c1" }, created_at: "" },
    { task_id: "t-1", sequence: 3, event_type: "task.finished", payload: { status: "COMPLETED" }, created_at: "" },
  ];
  const requests = stubFetch((url) => {
    if (url === "/api/v1/tasks/t-1") return json({ id: "t-1", status: "COMPLETED", event_sequence: 3, cancel_requested_at: null });
    if (url.startsWith("/api/v1/tasks/t-1/events/history")) return json(log.filter((event) => event.sequence > Number(new URL(url, "http://x").searchParams.get("after"))));
    return json({}, 404);
  });
  const listeners = new Set();
  globalThis.document = { hidden: false, addEventListener: (type, listener) => listeners.add(listener), removeEventListener: (type, listener) => listeners.delete(listener) };
  let answered = 0;
  const controller = createBrowserSync({ taskId: "t-1", savedAnswerReady: () => ++answered > 0 });
  controller.start();
  assert.equal(listeners.size, 1);
  for (let i = 0; i < 20 && controller.getSnapshot().phase !== "complete"; i++) await flush();
  const snapshot = controller.getSnapshot();
  assert.equal(snapshot.phase, "complete");
  assert.equal(snapshot.activity.contiguous, 3);
  assert.equal(snapshot.activity.view[0].label, "Searched the web");
  assert.equal(listeners.size, 0, "completion releases the visibility listener");
  assert.deepEqual(requests.map((request) => request.url), ["/api/v1/tasks/t-1", "/api/v1/tasks/t-1/events/history?after=0&limit=100"]);
  controller.stop();
});

test("createBrowserSync raises the app-wide event when the stream reports revocation", async () => {
  stubFetch(() => json({ id: "t-1", status: "RUNNING", event_sequence: 0, cancel_requested_at: null }));
  globalThis.document = { hidden: false, addEventListener() {}, removeEventListener() {} };
  const originalSource = globalThis.EventSource;
  FakeEventSource.instances = [];
  globalThis.EventSource = FakeEventSource;
  try {
    const lost = [];
    const controller = createBrowserSync({ taskId: "t-1", onSessionLost: (reason) => lost.push(reason) });
    controller.start();
    await flush();
    FakeEventSource.instances[0].fire("session.closed", {});
    assert.deepEqual(lost, ["revoked"]);
    assert.deepEqual(globalThis.window.events, ["agentforge-session-expired"]);
    assert.equal(controller.getSnapshot().halted, true);
    controller.stop();
  } finally {
    if (originalSource === undefined) delete globalThis.EventSource; else globalThis.EventSource = originalSource;
  }
});


test("reopened completed activity uses API-valid pages and restores every step", async () => {
  const b = builder({ style: "modern" });
  for (let i = 0; i < 125; i++) b.call("web_search", `search-${i}`);
  const requests = stubFetch((url) => {
    const params = new URL(url, "http://local").searchParams;
    const limit = Number(params.get("limit"));
    const after = Number(params.get("after"));
    if (limit < 1 || limit > 100) return json({ detail: [{ loc: ["query", "limit"], type: "less_than_equal" }] }, 422);
    return json(b.events.filter((event) => event.sequence > after).slice(0, limit));
  });
  const restore = () => loadHistoryEntries("task-1", { terminal: true }, (after, limit, signal) => fetchEventHistory("task-1", after, limit, { signal }));
  const first = await restore();
  assert.equal(summarizeEntries(first).actions, 125);
  assert.deepEqual(await restore(), first, "leaving and returning restores identical activity without live entries");
  assert.equal(requests.length, 6, "each restoration reads all three pages");
});
