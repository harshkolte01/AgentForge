"use client";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createConversationStore, type ConversationStore, type StoreState } from "@/features/sessions/conversation-store";
import type { ScrollController } from "@/lib/scroll-controller";
import { api, message, terminalStatuses } from "@/lib/task-api";
import type { ConversationMessage, ConversationSession } from "@/lib/workspace-api";

/**
 * A conversation's store (`conversation-store.ts`) wired to the page: it reads the conversation when it opens, again whenever the
 * server says it changed (`conversation.changed` over SSE), on a light poll while the tab is visible, and when the tab returns.
 * Everything is stopped when the thread unmounts, so nothing outlives the conversation. `onSession` must be stable (a parent
 * `useCallback`): the store keeps the first one it is given.
 */
export function useConversationStore(session: ConversationSession, refresh: number, onSession: (session: ConversationSession) => void, scroll: ScrollController): { store: ConversationStore; state: StoreState } {
  const sessionId = session.id;
  const [opened] = useState(() => session.revision ?? 0);
  const [store] = useState(() => createConversationStore({
    sessionId,
    initialSession: session,
    fetchSession: (signal) => api<ConversationSession>(`/sessions/${sessionId}`, { signal }),
    fetchPage: (after, limit, signal) => api<ConversationMessage[]>(`/sessions/${sessionId}/messages?after_sequence=${after}&limit=${limit}`, { signal }),
    onSession,
    isTerminal: (status) => terminalStatuses.has(status),
    describeError: message,
    beforePrepend: () => scroll.holdPlace(),
  }));
  const state = useSyncExternalStore(store.subscribe, store.getState, store.getState);
  useEffect(() => {
    store.start();
    void store.refresh();
    const source = typeof EventSource === "undefined" ? null : new EventSource(`/api/v1/sessions/${sessionId}/events?after=${opened}`);
    let timer: ReturnType<typeof setTimeout> | undefined;
    const soon = () => { if (!timer) timer = setTimeout(() => { timer = undefined; void store.refresh(); }, 150); };
    source?.addEventListener("conversation.changed", soon);
    source?.addEventListener("session.closed", () => { source.close(); window.dispatchEvent(new Event("agentforge-session-expired")); });
    // The stream is the fast path; this catches what it missed without reading every message again when nothing changed.
    const interval = setInterval(() => { if (!document.hidden) void store.refresh({ light: true }); }, 3000);
    const visible = () => { if (!document.hidden) soon(); };
    document.addEventListener("visibilitychange", visible);
    return () => {
      store.stop();
      source?.close();
      clearInterval(interval);
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [store, sessionId, opened]);
  // A refresh asked for by the workspace (after a send) once the conversation is open.
  const seenRefresh = useRef(refresh);
  useEffect(() => {
    if (seenRefresh.current === refresh) return;
    seenRefresh.current = refresh;
    void store.refresh();
  }, [refresh, store]);
  // The conversation's own active task, as the parent last knew it, is what the live view follows.
  useEffect(() => { store.observeSession(session); }, [store, session]);
  return { store, state };
}
