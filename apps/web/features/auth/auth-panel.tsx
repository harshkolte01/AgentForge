"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useId, useState, type FormEvent } from "react";
import { ArrowRight, LoaderCircle, LockKeyhole, RefreshCw } from "lucide-react";
import { GitHubMark } from "@/components/github-mark";
import { Button, buttonVariants } from "@/components/ui/button";
import type { AuthQuery } from "@/features/auth/auth-query";
import { authErrorMessage, SESSION_EXPIRED_MESSAGE } from "@/lib/auth-errors";
import { authPath, githubStartPath } from "@/lib/auth-redirect";
import { forgetAccount } from "@/lib/conversation-outbox";
import { routes } from "@/lib/routes";
import { api, message, type SessionInfo } from "@/lib/task-api";
import { useAuthSession } from "@/lib/use-auth-session";

const fieldClass = "w-full rounded-xl border border-input bg-background px-3.5 py-2.5 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50";
const linkClass = "rounded font-medium text-foreground underline decoration-foreground/30 underline-offset-4 outline-none hover:decoration-foreground focus-visible:ring-2 focus-visible:ring-ring/60";
const bigButton = "min-h-12 w-full rounded-lg text-[15px] font-medium focus-visible:ring-offset-2 focus-visible:ring-offset-background";

type Mode = "sign-in" | "sign-up";

const copy = {
  "sign-in": {
    title: "Welcome back", lead: "Sign in to pick up your conversations where you left them.",
    github: "Continue with GitHub", switchPrompt: "New to AgentForge?", switchLabel: "Create an account", switchTo: routes.signUp,
  },
  "sign-up": {
    title: "Create your account", lead: "Start your first conversation with AgentForge.",
    github: "Sign up with GitHub", switchPrompt: "Already have an account?", switchLabel: "Sign in", switchTo: routes.signIn,
  },
} as const;

/**
 * Sign in and sign up. AgentForge has no passwords: on a hosted workspace an account *is* a GitHub sign-in (the first one creates
 * it), and a self-hosted one can use a private local token. So both pages run the same flow and differ only in what they say.
 */
export function AuthPanel({ mode, query }: { mode: Mode; query: AuthQuery }) {
  const router = useRouter();
  const { state, retry } = useAuthSession();
  const target = query.next ?? routes.newConversation;
  const text = copy[mode];
  const notice = query.authError ? authErrorMessage(query.authError) : query.reason === "expired" ? SESSION_EXPIRED_MESSAGE : "";
  const signedIn = state.status === "ready" && state.session.authenticated;

  // The explanation is shown once; a reload should not repeat it.
  useEffect(() => {
    if (!query.authError && !query.reason) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("auth_error");
    url.searchParams.delete("reason");
    window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  }, [query.authError, query.reason]);

  // Someone who is already signed in has nothing to do here.
  useEffect(() => { if (signedIn) router.replace(target); }, [signedIn, router, target]);

  const session = state.status === "ready" ? state.session : null;
  return <section aria-labelledby="auth-title" className="w-full max-w-[390px] space-y-8">
    <header className="space-y-3">
      <h1 id="auth-title" className="text-balance text-3xl font-medium tracking-tight sm:text-4xl">{signedIn ? "You're signed in" : session && !session.enabled ? "Workspace access isn't set up" : session && session.mode !== "github_app" ? "Local access" : text.title}</h1>
      <p className="text-[15px] leading-7 text-muted-foreground">
        {signedIn ? "Taking you to your workspace…" : session && !session.enabled ? "Sign-in is not available yet. Contact the person who manages this workspace."
          : session && session.mode !== "github_app" ? "This workspace uses a private token instead of accounts. Enter it to continue." : text.lead}
      </p>
    </header>

    {notice ? <p role="alert" className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm leading-6 text-destructive">{notice}</p> : null}

    {state.status === "checking" ? <div role="status" aria-label="Checking how this workspace signs in" className="space-y-3"><div className="h-12 animate-pulse rounded-lg bg-muted" /><span className="sr-only">Checking…</span></div> : null}

    {state.status === "unreachable" ? <div className="space-y-4">
      <p role="alert" className="text-sm leading-6 text-destructive">Sign-in is temporarily unavailable.</p>
      <p className="text-sm leading-6 text-muted-foreground">Please try again in a moment.</p>
      <Button variant="outline" onClick={retry}><RefreshCw />Retry connection</Button>
    </div> : null}

    {signedIn ? <Link href={target} className={`${buttonVariants()} ${bigButton} gap-2`}>Continue<ArrowRight className="size-4" aria-hidden="true" /></Link> : null}

    {session && !session.authenticated ? (session.enabled
      ? session.mode === "github_app" ? <GitHubSignIn label={text.github} next={query.next} />
        : <TokenForm onDone={() => router.replace(target)} />
      : <div className="space-y-2 text-sm leading-6 text-muted-foreground">
        <Button variant="outline" onClick={retry} className="mt-2"><RefreshCw />Check again</Button>
      </div>) : null}

    {session && !session.authenticated && session.enabled && session.mode === "github_app" ? <p className="text-xs leading-5 text-muted-foreground">
      Connect a repository whenever you’re ready.
    </p> : null}

    {session && !session.authenticated && session.enabled && session.mode === "github_app" ? <p className="border-t pt-6 text-sm text-muted-foreground">
      {text.switchPrompt} <Link href={authPath(text.switchTo, query.next)} className={linkClass}>{text.switchLabel}</Link>
    </p> : null}
  </section>;
}

function GitHubSignIn({ label, next }: { label: string; next: string | null }) {
  const [leaving, setLeaving] = useState(false);
  // Coming back with the Back button restores this page as it was: the button must work again.
  useEffect(() => {
    const restore = (event: PageTransitionEvent) => { if (event.persisted) setLeaving(false); };
    window.addEventListener("pageshow", restore);
    return () => window.removeEventListener("pageshow", restore);
  }, []);
  return <a href={githubStartPath(next)} aria-disabled={leaving} onClick={(event) => { if (leaving) event.preventDefault(); else setLeaving(true); }}
    className={`${buttonVariants()} ${bigButton} gap-2.5 ${leaving ? "pointer-events-none opacity-70" : ""}`}>
    {leaving ? <LoaderCircle className="size-4 animate-spin" aria-hidden="true" /> : <GitHubMark className="size-[18px]" />}
    {leaving ? "Opening GitHub…" : label}
  </a>;
}

function TokenForm({ onDone }: { onDone: () => void }) {
  const id = useId();
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function unlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await api<void>("/auth/session", { method: "POST", headers: { Authorization: `Bearer ${token}` } });
      forgetAccount();
      setToken("");
      const confirmed = await api<SessionInfo>("/auth/session");
      if (confirmed.authenticated) onDone(); else setError("The token was accepted, but the session did not start. Try again.");
    } catch (failure) { setError(message(failure)); }
    finally { setBusy(false); }
  }
  return <form onSubmit={unlock} className="space-y-4">
    <div className="space-y-2">
      <label htmlFor={id} className="flex items-center gap-2 text-sm font-medium"><LockKeyhole className="size-4" aria-hidden="true" />Access token</label>
      <input id={id} type="password" autoComplete="off" required minLength={32} maxLength={512} value={token} onChange={(event) => setToken(event.target.value)} disabled={busy} className={fieldClass} aria-describedby={error ? `${id}-error` : undefined} aria-invalid={error ? true : undefined} />
      <p className="text-xs leading-5 text-muted-foreground">Use the access token provided by your workspace administrator.</p>
    </div>
    {error ? <p id={`${id}-error`} role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
    <Button type="submit" className={bigButton} disabled={busy || token.length < 32}>{busy ? "Unlocking…" : "Unlock workspace"}</Button>
  </form>;
}
