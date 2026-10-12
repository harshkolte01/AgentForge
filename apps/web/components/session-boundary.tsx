"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { RefreshCw, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { BrandMark } from "@/components/brand-mark";
import { authErrorMessage } from "@/lib/auth-errors";
import { authPath } from "@/lib/auth-redirect";
import { forgetAccount, primeAccount } from "@/lib/conversation-outbox";
import { routes } from "@/lib/routes";
import { api, message, type SessionInfo } from "@/lib/task-api";

export const inputClass = "w-full rounded-lg border border-input bg-background px-3 py-2 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:opacity-50";
const AuthContext = createContext<{ session: SessionInfo; logout: () => Promise<void>; busy: boolean } | null>(null);
export function useWorkspaceIdentity() { return useContext(AuthContext); }

/** Reads `?auth_error=` once and drops it from the address, so a reload does not show the error again. */
function takeAuthError(): string {
  const url = new URL(window.location.href);
  const code = url.searchParams.get("auth_error");
  if (code === null) return "";
  url.searchParams.delete("auth_error");
  window.history.replaceState(window.history.state, "", `${url.pathname}${url.search}${url.hash}`);
  return authErrorMessage(code.slice(0, 64));
}

/**
 * The signed-in app's one session check. Anyone who is not signed in is sent to `/sign-in`, carrying the page they wanted (so they
 * come back to it) and, when the GitHub callback or an expired session brought them here, why. The API's callback redirects to
 * `/chat?auth_error=…`; that address still works and is forwarded from here.
 */
export function SessionBoundary({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // The session could not be read at all (as opposed to still being read).
  const [unreachable, setUnreachable] = useState(false);
  const [reload, setReload] = useState(0);
  const expired = useRef(false);
  // Set once a redirect is under way, so a second render never starts another.
  const leaving = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    void api<SessionInfo>("/auth/session", { signal: controller.signal }).then((value) => {
      if (controller.signal.aborted) return;
      // The outbox needs the same account id; hand it over before the workspace mounts and asks for it.
      if (value.authenticated && value.user?.id) primeAccount(value.user.id);
      setSession(value); setUnreachable(false);
      if (value.authenticated) {
        const failed = takeAuthError();
        if (failed) setError(failed);
      }
    }).catch((error: unknown) => { if (!controller.signal.aborted) { setError(message(error)); setUnreachable(true); } });
    const onExpired = () => {
      forgetAccount();
      // Per-person stores (plugins, skills, approval mode, reasoning effort) must not hand the next person this one's values.
      window.dispatchEvent(new Event("agentforge-account-changed"));
      expired.current = true;
      setSession(null);
      setReload((value) => value + 1);
    };
    window.addEventListener("agentforge-session-expired", onExpired);
    return () => { controller.abort(); window.removeEventListener("agentforge-session-expired", onExpired); };
  }, [reload]);

  useEffect(() => {
    if (!session || session.authenticated || leaving.current) return;
    leaving.current = true;
    const here = new URL(window.location.href);
    const code = here.searchParams.get("auth_error");
    here.searchParams.delete("auth_error");
    const extra: Record<string, string> = {};
    if (code) extra.auth_error = code.slice(0, 64);
    if (expired.current) extra.reason = "expired";
    router.replace(authPath(routes.signIn, `${here.pathname}${here.search}${here.hash}`, extra));
  }, [session, router]);

  async function logout() {
    setBusy(true); setError("");
    try {
      await api<void>("/auth/logout", { method: "POST" });
      forgetAccount();
      // Signing out is a choice, not an interruption: the landing page, not a sign-in prompt.
      leaving.current = true;
      setSession((value) => value ? { ...value, authenticated: false, user: undefined } : null);
      router.replace(routes.home);
    } catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }

  // Signed in: the app, with any failure shown as a floating notice so it never pushes the full-height workspace down.
  if (session?.authenticated) return <AuthContext.Provider value={{ session, logout, busy }}>
    {children}
    {error ? <div role="alert" className="fixed inset-x-4 bottom-4 z-50 mx-auto flex max-w-md items-start gap-3 rounded-xl border bg-popover px-4 py-3 text-sm text-destructive shadow-lg"><p className="min-w-0 flex-1 leading-6">{error}</p><Button variant="ghost" size="icon-sm" aria-label="Dismiss" onClick={() => setError("")}><X /></Button></div> : null}
  </AuthContext.Provider>;

  // Still checking (or on its way to sign in), and nothing went wrong: a quiet blank page (a mark fades in only if it takes a while).
  if (!unreachable) return <div role="status" aria-label="Loading AgentForge" className="flex min-h-svh items-center justify-center bg-background">
    <span className="motion-safe:animate-in motion-safe:fade-in motion-safe:duration-500 [animation-delay:400ms] [animation-fill-mode:both]"><BrandMark className="size-8 text-sm opacity-50" /></span>
  </div>;

  return <main className="flex min-h-svh items-center justify-center bg-background px-6 py-16">
    <section className="w-full max-w-sm space-y-7" aria-labelledby="session-title">
      <Link href={routes.home} className="inline-flex items-center gap-2 rounded-md text-sm font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring/60"><BrandMark className="size-8 text-sm" />AgentForge</Link>
      <div className="space-y-3"><h1 id="session-title" className="text-3xl font-semibold tracking-tight">Can&apos;t reach your workspace.</h1><p className="text-sm leading-6 text-muted-foreground">Check that the AgentForge API is running, then try again.</p></div>
      {error ? <p role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
      <Button variant="outline" onClick={() => { setError(""); setSession(null); setUnreachable(false); setReload((value) => value + 1); }}><RefreshCw />Retry connection</Button>
    </section>
  </main>;
}
