"use client";

import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { LockKeyhole } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, message, type SessionInfo } from "@/lib/task-api";

export const inputClass = "w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50";

export function SessionBoundary({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void api<SessionInfo>("/auth/session", { signal: controller.signal }).then(setSession).catch((error: unknown) => {
      if (!controller.signal.aborted) setError(message(error));
    });
    const expired = () => {
      setSession({ enabled: true, authenticated: false, mode: "local_single_user" });
      setError("Your local session has expired. Unlock the workspace again.");
    };
    window.addEventListener("agentforge-session-expired", expired);
    return () => {
      controller.abort();
      window.removeEventListener("agentforge-session-expired", expired);
    };
  }, [reload]);

  async function unlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api<void>("/auth/session", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
      setToken("");
      setSession(await api<SessionInfo>("/auth/session"));
    } catch (error) {
      setError(message(error));
    } finally {
      setBusy(false);
    }
  }

  if (session?.authenticated) {
    return (
      <>
        <div className="mb-8 flex flex-wrap items-center justify-between gap-3 border-b pb-4 text-sm">
          <p className="text-muted-foreground">{session.user?.display_name ?? "Local workspace"} <span className="mx-2" aria-hidden="true">·</span> Local session</p>
          <Button variant="ghost" size="sm" disabled={busy} onClick={async () => {
            setBusy(true);
            try {
              await api<void>("/auth/logout", { method: "POST" });
              setSession({ enabled: true, authenticated: false, mode: "local_single_user" });
            } catch (error) { setError(message(error)); }
            finally { setBusy(false); }
          }}>Lock workspace</Button>
        </div>
        {error ? <p role="alert" className="mb-5 text-sm text-destructive">{error}</p> : null}
        {children}
      </>
    );
  }

  return (
    <section className="mx-auto max-w-lg space-y-6 py-8" aria-labelledby="session-title">
      <LockKeyhole aria-hidden="true" className="size-7 text-muted-foreground" />
      <div className="space-y-2">
        <h1 id="session-title" className="text-2xl font-semibold tracking-tight">{session?.enabled ? "Unlock your workspace" : session ? "Set up local access" : "Connecting to your workspace"}</h1>
        <p className="text-sm leading-relaxed text-muted-foreground">{session?.enabled ? "Enter the bootstrap token from your private API configuration. Your local session expires after the configured duration." : session ? "Task access is disabled until a local identity and bootstrap token are configured. Follow docs/phase-3.md, restart the API, then reconnect." : "Checking the local API and session configuration."}</p>
      </div>
      {!session && !error ? <div aria-label="Loading session" className="h-10 rounded-lg bg-muted" /> : null}
      {session?.enabled ? (
        <form onSubmit={unlock} className="space-y-4">
          <div className="space-y-2">
            <label htmlFor="bootstrap-token" className="text-sm font-medium">Bootstrap token</label>
            <input id="bootstrap-token" type="password" autoComplete="off" required minLength={32} maxLength={512} value={token} onChange={(event) => setToken(event.target.value)} className={inputClass} aria-describedby="token-help" disabled={busy} />
            <p id="token-help" className="text-xs text-muted-foreground">Local access only. API restart or session expiry requires unlocking again.</p>
          </div>
          <Button type="submit" disabled={busy}>{busy ? "Unlocking…" : "Unlock workspace"}</Button>
        </form>
      ) : null}
      {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
      {!session?.enabled ? <Button variant="outline" onClick={() => { setError(""); setSession(null); setReload((value) => value + 1); }}>Reconnect</Button> : null}
    </section>
  );
}
