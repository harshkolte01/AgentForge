/**
 * The browser wiring for the recovery controller: the real requests, EventSource and page visibility. Components create
 * one controller per followed task (`createBrowserSync`) and read it with `useSyncExternalStore`; everything testable
 * lives in `sync.ts`, so this file only adapts browser APIs to its injected dependencies.
 */
import { createSyncController, type StreamConnection, type StreamHandlers, type SyncController } from "@/lib/activity/sync";
import { parseLiveDelta } from "@/lib/activity/live-text";
import type { ActivityOptions, TaskEvent } from "@/lib/activity/types";
import { api, eventTypes, fetchEventHistory, fetchStreamSnapshot, terminalStatuses, type Task } from "@/lib/task-api";

type EventSourceLike = Pick<EventSource, "addEventListener" | "close" | "readyState"> & { onopen: ((event: Event) => void) | null; onerror: ((event: Event) => void) | null };
type EventSourceFactory = { new (url: string): EventSourceLike; readonly CLOSED: number };

const parse = (raw: Event): unknown => {
  try { return JSON.parse((raw as MessageEvent<string>).data); } catch { return null; }
};

/**
 * Opens `/tasks/{id}/events` and routes its frames to the handlers. A browser reconnects by itself after a dropped
 * connection; after a refusal (readyState CLOSED) it gives up, and `onDisconnect` lets the controller reopen it.
 */
export function openEventStream(taskId: string, after: number, handlers: StreamHandlers, Source: EventSourceFactory = EventSource as unknown as EventSourceFactory): StreamConnection {
  const source = new Source(`/api/v1/tasks/${encodeURIComponent(taskId)}/events?after=${after}`);
  let closed = false;
  source.onopen = () => { if (!closed) handlers.onOpen(); };
  for (const type of eventTypes) {
    source.addEventListener(type, (raw) => {
      const event = parse(raw) as TaskEvent | null;
      if (!closed && event && typeof event.sequence === "number") handlers.onEvent(event);
    });
  }
  source.addEventListener("message.delta", (raw) => {
    const delta = parseLiveDelta(parse(raw));
    if (!closed && delta) handlers.onDelta(delta);
  });
  source.addEventListener("session.closed", () => {
    if (closed) return;
    closed = true;
    source.close();
    handlers.onSessionClosed();
  });
  source.onerror = () => {
    if (closed || source.readyState !== Source.CLOSED) return;
    closed = true;
    handlers.onDisconnect();
  };
  return { close() { closed = true; source.close(); } };
}

export type BrowserSyncOptions = {
  taskId: string;
  /** Whether the saved answer for the task is in place (the thread has refreshed and holds the assistant message). */
  savedAnswerReady?: (task: Task) => boolean | Promise<boolean>;
  /** Called when the session is revoked or expired, after the app-wide `agentforge-session-expired` event was raised. */
  onSessionLost?: (reason: "revoked" | "unauthorized") => void;
  activity?: ActivityOptions;
};

/** A controller wired to the real API, stream and page visibility. Call `start()` to begin and `stop()` on cleanup. */
export function createBrowserSync({ taskId, savedAnswerReady, onSessionLost, activity }: BrowserSyncOptions): SyncController<Task> {
  return createSyncController<Task>({
    taskId,
    activity,
    fetchTask: (signal) => api<Task>(`/tasks/${encodeURIComponent(taskId)}`, { signal }),
    fetchHistory: (after, limit, signal) => fetchEventHistory(taskId, after, limit, { signal }),
    fetchSnapshot: (signal) => fetchStreamSnapshot(taskId, { signal }),
    openStream: typeof EventSource === "undefined" ? undefined : (after, handlers) => openEventStream(taskId, after, handlers),
    savedAnswerReady,
    isTerminalStatus: (status) => terminalStatuses.has(status),
    // A tab in the background is throttled; when it comes back, read everything it missed before the next poll is due.
    onVisible: (listener) => {
      const resume = () => { if (!document.hidden) listener(); };
      document.addEventListener("visibilitychange", resume);
      return () => document.removeEventListener("visibilitychange", resume);
    },
    onSessionLost: (reason) => {
      // `api()` raises the event itself for a 401; a revoked stream is only known here.
      if (reason === "revoked") window.dispatchEvent(new Event("agentforge-session-expired"));
      onSessionLost?.(reason);
    },
  });
}
