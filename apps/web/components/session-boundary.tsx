"use client";

import { createContext, useContext, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { GitBranch as Github, LockKeyhole, RefreshCw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/brand-mark";
import { forgetAccount } from "@/lib/conversation-outbox";
import { api, message, type SessionInfo } from "@/lib/task-api";

export const inputClass = "w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50";
const AuthContext = createContext<{ session: SessionInfo; logout: () => Promise<void>; busy: boolean } | null>(null);
export function useWorkspaceIdentity() { return useContext(AuthContext); }

/** What each `?auth_error=` code from the GitHub sign-in callback means for the person (see `apps/api/.../auth/routes.py`). */
const authErrors: Record<string, string> = {
  denied: "You cancelled GitHub sign-in.",
  expired: "The sign-in link expired. Start sign-in again.",
  signup_closed: "New accounts are not open on this workspace yet.",
  not_allowed: "This GitHub account isn't allowed on this workspace.",
  app_misconfigured: "The GitHub App is misconfigured. The operator should enable expiring user tokens for it (see docs/github-app.md).",
  unavailable: "GitHub couldn't be reached. Try again in a moment.",
  rate_limited: "Too many sign-in attempts. Wait a minute, then try again.",
};
export function authErrorMessage(code: string): string {
  return authErrors[code] ?? "GitHub sign-in could not be completed. Start sign-in again.";
}
const BOOTSTRAP_COMMAND = "cd apps/api && uv run python -m agentforge.auth.bootstrap --write-env";

/** Reads `?auth_error=` once and drops it from the address, so a reload does not show the error again. */
function takeAuthError(): string {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("auth_error");
  if (code === null) return "";
  url.searchParams.delete("auth_error");
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  return authErrorMessage(code.slice(0, 64));
}

export function SessionBoundary({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // The session could not be read at all (as opposed to still being read).
  const [unreachable, setUnreachable] = useState(false);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    void api<SessionInfo>("/auth/session", { signal: controller.signal }).then((value) => {
      if (controller.signal.aborted) return;
      setSession(value); setUnreachable(false);
      const failed = takeAuthError();
      if (failed) setError(failed);
    }).catch((error: unknown) => { if (!controller.signal.aborted) { setError(message(error)); setUnreachable(true); } });
    const expired = () => {
      forgetAccount();
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
      forgetAccount();
      setSession((value) => value ? { ...value, authenticated: false, user: undefined } : null);
    } catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }
  async function unlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await api<void>("/auth/session", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
      forgetAccount();
      setToken(""); setSession(await api<SessionInfo>("/auth/session"));
    } catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }

  // Signed in: the app, with any failure shown as a floating notice so it never pushes the full-height workspace down.
  if (session?.authenticated) return <AuthContext.Provider value={{ session, logout, busy }}>
    {children}
    {error ? <div role="alert" className="fixed inset-x-4 bottom-4 z-50 mx-auto flex max-w-md items-start gap-3 rounded-xl border bg-popover px-4 py-3 text-sm text-destructive shadow-lg"><p className="min-w-0 flex-1 leading-6">{error}</p><Button variant="ghost" size="icon-sm" aria-label="Dismiss" onClick={() => setError("")}><X /></Button></div> : null}
  </AuthContext.Provider>;

  // Still checking, and nothing went wrong: a quiet blank page (a mark fades in only if it takes a while), never sign-in copy.
  if (!session && !unreachable) return <div role="status" aria-label="Loading AgentForge" className="flex min-h-svh items-center justify-center bg-background">
    <span className="motion-safe:animate-in motion-safe:fade-in motion-safe:duration-500 [animation-delay:400ms] [animation-fill-mode:both]"><BrandMark className="size-8 text-sm opacity-50" /></span>
  </div>;

  const github = session?.mode === "github_app";
  return <main className="flex min-h-svh items-center justify-center bg-background px-6 py-16">
    <section className="w-full max-w-sm space-y-7" aria-labelledby="session-title">
      <p className="inline-flex items-center gap-2 text-sm font-semibold"><BrandMark className="size-8 text-sm" />AgentForge</p>
      <div className="space-y-3"><h1 id="session-title" className="text-3xl font-semibold tracking-tight">{github ? "Your next idea starts here." : session?.enabled ? "Welcome to your workspace." : session ? "Workspace access is not configured." : "Can't reach your workspace."}</h1><p className="text-sm leading-6 text-muted-foreground">{github ? "Sign up or sign in with GitHub to research, solve problems and work on code in one place." : session?.enabled ? "Use your private bootstrap token to continue with local access." : session ? "An operator needs to enable GitHub sign-in or local access before you can continue." : "Check that the AgentForge API is running, then try again."}</p></div>
      {github ? <a href="/api/v1/auth/github/start" className="flex min-h-11 items-center justify-center gap-2 rounded-lg bg-primary px-4 py-3 text-sm font-medium text-primary-foreground outline-none hover:bg-primary/85 focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background"><Github className="size-4" aria-hidden="true" />Continue with GitHub</a> : session?.enabled ? <form onSubmit={unlock} className="space-y-4"><div className="space-y-2"><label htmlFor="bootstrap-token" className="flex items-center gap-2 text-sm font-medium"><LockKeyhole className="size-4" aria-hidden="true" />Bootstrap token</label><input id="bootstrap-token" type="password" autoComplete="off" required minLength={32} maxLength={512} value={token} onChange={(event) => setToken(event.target.value)} className={inputClass} disabled={busy} /></div><Button type="submit" className="min-h-10 w-full" disabled={busy}>{busy ? "Unlocking…" : "Unlock workspace"}</Button></form> : null}
      {github ? <p className="text-xs leading-5 text-muted-foreground">Signing in creates your account. Repository access is a separate choice, and in Ask me mode nothing is published without your approval.</p> : null}
      {session && !session.enabled ? <div className="space-y-2 text-sm leading-6 text-muted-foreground"><p>For local access, create a token and restart the API:</p><pre className="overflow-x-auto rounded-lg bg-muted px-3 py-2 font-mono text-xs leading-5 text-foreground">{BOOTSTRAP_COMMAND}</pre></div> : null}
      {error ? <p role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
      {/* Never while loading; with GitHub sign-in on screen, "Continue with GitHub" is the way to try again. */}
      {(session && !session.enabled) || (error && !github) ? <Button variant="outline" onClick={() => { setError(""); setSession(null); setUnreachable(false); setReload((value) => value + 1); }}><RefreshCw />Retry connection</Button> : null}
    </section>
  </main>;
}
