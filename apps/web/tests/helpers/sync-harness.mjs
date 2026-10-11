// A fake server and stream for the recovery controller. Time comes from fakeClock, so nothing here is real I/O.
import { createSyncController } from "../../lib/activity/sync.ts";
import { fakeClock, flush } from "./clock.mjs";

const TERMINAL = new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"]);

export function abortableDelay(signal, promise) {
  if (!signal) return promise;
  return new Promise((resolve, reject) => {
    const abort = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    if (signal.aborted) return abort();
    signal.addEventListener("abort", abort, { once: true });
    promise.then(resolve, reject);
  });
}

export function fakeServer({ taskId = "task-1" } = {}) {
  const server = {
    taskId,
    log: [],
    status: "RUNNING",
    cancelRequestedAt: null,
    answerReady: true,
    live: { task_id: taskId, epoch: 0, revision: 0, kind: "progress", text: "", event_sequence: 0, terminal: false },
    calls: { task: 0, history: [], snapshot: 0, answer: 0 },
    /** The next N calls of that kind throw (an Error, with `status` when given). */
    failNext: { task: 0, history: 0, snapshot: 0, answer: 0 },
    failStatus: undefined,
    /** A promise a call waits on before answering (to hold a request in flight). */
    hold: {},
    pageCap: Infinity,
    append(...events) {
      for (const event of events.flat()) if (!server.log.some((known) => known.sequence === event.sequence)) server.log.push(event);
      server.log.sort((a, b) => a.sequence - b.sequence);
    },
    finish(status = "COMPLETED") { server.status = status; },
    watermark: () => server.log.reduce((top, event) => Math.max(top, event.sequence), 0),
    maybeFail(kind) {
      if (server.failNext[kind] > 0) {
        server.failNext[kind]--;
        throw Object.assign(new Error(`${kind} failed`), server.failStatus ? { status: server.failStatus } : {});
      }
    },
    async fetchTask(signal) {
      server.calls.task++;
      await abortableDelay(signal, Promise.resolve(server.hold.task));
      server.maybeFail("task");
      return { id: taskId, status: server.status, event_sequence: server.watermark(), cancel_requested_at: server.cancelRequestedAt };
    },
    async fetchHistory(after, limit, signal) {
      server.calls.history.push([after, limit]);
      await abortableDelay(signal, Promise.resolve(server.hold.history));
      server.maybeFail("history");
      return server.log.filter((event) => event.sequence > after).slice(0, Math.min(limit, server.pageCap));
    },
    async fetchSnapshot(signal) {
      server.calls.snapshot++;
      await abortableDelay(signal, Promise.resolve(server.hold.snapshot));
      server.maybeFail("snapshot");
      return { ...server.live, event_sequence: server.watermark(), terminal: TERMINAL.has(server.status) };
    },
    async savedAnswerReady() {
      server.calls.answer++;
      server.maybeFail("answer");
      return typeof server.answerReady === "function" ? server.answerReady() : server.answerReady;
    },
  };
  return server;
}

/** Fake EventSource-like connections: tests push frames through `emit*`. */
export function fakeStreams() {
  const streams = {
    connections: [],
    openStream(after, handlers) {
      const connection = { after, handlers, closed: false, close() { connection.closed = true; } };
      streams.connections.push(connection);
      return connection;
    },
    get current() { return streams.connections.filter((connection) => !connection.closed).at(-1); },
    open() { streams.current.handlers.onOpen(); },
    event(event) { streams.current.handlers.onEvent(event); },
    delta(delta) { streams.current.handlers.onDelta(delta); },
    disconnect() { streams.current.handlers.onDisconnect(); },
    sessionClosed() { streams.current.handlers.onSessionClosed(); },
  };
  return streams;
}

export function makeSync({ server = fakeServer(), withStream = true, options = {}, ...overrides } = {}) {
  const clock = fakeClock();
  const streams = fakeStreams();
  const lost = [];
  const history = [];
  const controller = createSyncController({
    taskId: server.taskId,
    fetchTask: (signal) => server.fetchTask(signal),
    fetchHistory: (after, limit, signal) => server.fetchHistory(after, limit, signal),
    fetchSnapshot: (signal) => server.fetchSnapshot(signal),
    openStream: withStream ? (after, handlers) => streams.openStream(after, handlers) : undefined,
    savedAnswerReady: () => server.savedAnswerReady(),
    isTerminalStatus: (status) => TERMINAL.has(status),
    clock,
    onVisible: (listener) => { visible.listeners.add(listener); return () => visible.listeners.delete(listener); },
    onSessionLost: (reason) => lost.push(reason),
    options,
    ...overrides,
  });
  const visible = { listeners: new Set(), show() { for (const listener of [...visible.listeners]) listener(); } };
  controller.subscribe(() => history.push(controller.getSnapshot()));
  return { controller, clock, server, streams, lost, visible, history, snap: () => controller.getSnapshot(), flush };
}
