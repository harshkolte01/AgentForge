"use client";

import { createContext, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { GitBranch as Github, LockKeyhole, LogOut, RefreshCw } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { api, message, type SessionInfo } from "@/lib/task-api";

export const inputClass = "w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50";
const AuthContext = createContext<{ session: SessionInfo; logout: () => Promise<void>; busy: boolean } | null>(null);
export function useWorkspaceIdentity() { return useContext(AuthContext); }

export function AccountControl() {
  const identity = useWorkspaceIdentity();
  if (!identity) return null;
  return <div className="flex min-w-0 items-center gap-2">
    <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-secondary text-xs font-semibold" aria-hidden="true">{identity.session.user?.display_name?.slice(0, 1).toUpperCase() ?? "A"}</span>
    <div className="min-w-0 flex-1"><p className="truncate text-sm font-medium">{identity.session.user?.display_name ?? "Workspace"}</p><p className="text-xs text-muted-foreground">{identity.session.mode === "github_app" ? "GitHub account" : "Local access"}</p></div>
    <Button variant="ghost" size="icon" aria-label="Sign out" disabled={identity.busy} onClick={() => void identity.logout()}><LogOut /></Button>
  </div>;
}

export function SessionBoundary({ children, embedded = false }: { children: ReactNode; embedded?: boolean }) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void api<SessionInfo>("/auth/session", { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) {
        setSession(value);
        if (new URLSearchParams(window.location.search).get("auth_error")) setError("GitHub sign-in could not be completed. Start sign-in again; if it continues, check the GitHub app configuration.");
      }
    }).catch((error: unknown) => { if (!controller.signal.aborted) setError(message(error)); });
    const expired = () => {
      setSession(null);
      setToken("");
      setError("Your session has expired. Sign in again to continue.");
      setReload((value) => value + 1);
    };
    window.addEventListener("agentforge-session-expired", expired);
    return () => { controller.abort(); window.removeEventListener("agentforge-session-expired", expired); };
  }, [reload]);

  async function logout() {
    setBusy(true); setError("");
    try {
      await api<void>("/auth/logout", { method: "POST" });
      setSession((value) => value ? { ...value, authenticated: false, user: undefined } : null);
    } catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }
  async function unlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await api<void>("/auth/session", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
      setToken(""); setSession(await api<SessionInfo>("/auth/session"));
    } catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }

  if (session?.authenticated) return <AuthContext.Provider value={{ session, logout, busy }}>
    {!embedded ? <div className="mb-8 flex items-center justify-between border-b pb-4"><AccountControl /></div> : null}
    {error ? <p role="alert" className="border-b px-5 py-3 text-sm text-destructive">{error}</p> : null}{children}
  </AuthContext.Provider>;

  const github = session?.mode === "github_app";
  return <main className="flex min-h-[80svh] items-center justify-center px-6 py-16">
    <section className="w-full max-w-sm space-y-7" aria-labelledby="session-title">
      <Link href="/" className="inline-flex items-center gap-2 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-4"><span className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">A</span>AgentForge AI</Link>
      <div className="space-y-3"><h1 id="session-title" className="text-3xl font-semibold tracking-tight">{github ? "Your next idea starts here." : session?.enabled ? "Welcome to your workspace." : session ? "Workspace access is not configured." : "Opening your workspace…"}</h1><p className="text-sm leading-6 text-muted-foreground">{github ? "Sign up or sign in with GitHub to research, solve problems and work on code in one place." : session?.enabled ? "Use your private bootstrap token to continue with local access." : session ? "An operator needs to enable GitHub sign-in or local access before you can continue." : "Checking your session and account configuration."}</p></div>
      {github ? <a href="/api/v1/auth/github/start" className="flex min-h-11 items-center justify-center gap-2 rounded-lg bg-primary px-4 py-3 text-sm font-medium text-primary-foreground outline-none hover:bg-primary/85 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"><Github className="size-4" aria-hidden="true" />Continue with GitHub</a> : session?.enabled ? <form onSubmit={unlock} className="space-y-4"><div className="space-y-2"><label htmlFor="bootstrap-token" className="flex items-center gap-2 text-sm font-medium"><LockKeyhole className="size-4" aria-hidden="true" />Bootstrap token</label><input id="bootstrap-token" type="password" autoComplete="off" required minLength={32} maxLength={512} value={token} onChange={(event) => setToken(event.target.value)} className={inputClass} disabled={busy} /></div><Button type="submit" className="min-h-10 w-full" disabled={busy}>{busy ? "Unlocking…" : "Unlock workspace"}</Button></form> : null}
      {github ? <p className="text-xs leading-5 text-muted-foreground">Signing in creates your account. Repository access is a separate choice, and publishing code always requires your approval.</p> : null}
      {error ? <p role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
      {!session?.enabled || error ? <Button variant="outline" onClick={() => { setError(""); setSession(null); setReload((value) => value + 1); }}><RefreshCw />Retry connection</Button> : null}
    </section>
  </main>;
}
