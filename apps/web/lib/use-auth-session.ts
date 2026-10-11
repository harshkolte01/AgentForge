"use client";
import { useCallback, useEffect, useState } from "react";
import { api, message, type SessionInfo } from "@/lib/task-api";

export type AuthState =
  | { status: "checking" }
  | { status: "ready"; session: SessionInfo }
  | { status: "unreachable"; error: string };

/** Reads who is signed in, once, for the public pages. `retry` asks again (after the API was down). */
export function useAuthSession(): { state: AuthState; retry: () => void } {
  const [state, setState] = useState<AuthState>({ status: "checking" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void api<SessionInfo>("/auth/session", { signal: controller.signal }).then(
      (session) => { if (!controller.signal.aborted) setState({ status: "ready", session }); },
      (failure: unknown) => { if (!controller.signal.aborted) setState({ status: "unreachable", error: message(failure) }); },
    );
    return () => controller.abort();
  }, [attempt]);
  const retry = useCallback(() => { setState({ status: "checking" }); setAttempt((value) => value + 1); }, []);
  return { state, retry };
}
