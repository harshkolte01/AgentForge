"use client";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/task-api";
import type { ContextStatus } from "@/lib/context";

const LIVE_REFRESH_MS = 5000;

/**
 * The context status for one conversation (or, with no id, a conversation not started yet).
 *
 * It reloads when the conversation changes, whenever `revision` changes (a turn started or finished),
 * on demand, and every few seconds while a run is live so the figure follows the agent's work. The last
 * good value stays on screen during a reload; a failure keeps it and only reports `failed`.
 */
export function useContextStatus(sessionId: string | null, revision: string, live: boolean) {
  const key = sessionId ?? "new";
  const [loaded, setLoaded] = useState<{ key: string; status: ContextStatus } | null>(null);
  const [failedKey, setFailedKey] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void api<ContextStatus>(`/context${sessionId ? `?session_id=${sessionId}` : ""}`, { signal: controller.signal })
      .then((status) => { if (!controller.signal.aborted) { setLoaded({ key, status }); setFailedKey(null); } })
      .catch(() => { if (!controller.signal.aborted) setFailedKey(key); });
    return () => controller.abort();
  }, [sessionId, key, revision, tick]);
  useEffect(() => {
    if (!live) return;
    const timer = window.setInterval(() => setTick((value) => value + 1), LIVE_REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [live]);
  const refresh = useCallback(() => setTick((value) => value + 1), []);
  return { status: loaded?.key === key ? loaded.status : null, failed: failedKey === key, refresh };
}
