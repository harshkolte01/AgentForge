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
    let timer: ReturnType<typeof setTimeout> | undefined;
    void api<ContextStatus>(`/context${sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ""}`, {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12_000)]),
    })
      .then((status) => { if (!controller.signal.aborted) { setLoaded({ key, status }); setFailedKey(null); } })
      .catch(() => { if (!controller.signal.aborted) setFailedKey(key); })
      .finally(() => {
        // Schedule after completion: slow requests aren't repeatedly aborted by the polling clock.
        if (!controller.signal.aborted && live && !document.hidden) timer = setTimeout(() => setTick((value) => value + 1), LIVE_REFRESH_MS);
      });
    return () => { controller.abort(); clearTimeout(timer); };
  }, [sessionId, key, revision, tick, live]);
  useEffect(() => {
    const resume = () => { if (!document.hidden) setTick((value) => value + 1); };
    document.addEventListener("visibilitychange", resume);
    return () => document.removeEventListener("visibilitychange", resume);
  }, []);
  const refresh = useCallback(() => setTick((value) => value + 1), []);
  return { status: loaded?.key === key ? loaded.status : null, failed: failedKey === key, refresh };
}
