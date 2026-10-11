"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { CircleCheck, Lock, RefreshCw, Search, TriangleAlert } from "lucide-react";
import { GitHubMark } from "@/components/github-mark";
import { Button } from "@/components/ui/button";
import { cardClass, iconTileClass, SectionHeader } from "@/features/settings/settings-ui";
import { useWorkspaceIdentity } from "@/components/session-boundary";
import { ApiError, api, message } from "@/lib/task-api";
import { githubInstallUrl, type GitHubRepository, type GitHubStatus } from "@/lib/github-api";
import { repositoryCache } from "@/lib/github-repositories-cache";

const SIGN_IN = "/api/v1/auth/github/start";
/** What the approval mode means for publishing (Settings → Agent). */
const PUBLISHING = "In Ask me mode, opening a pull request waits for your approval and shows the files it changes. Approve for me skips approvals.";
const SEARCH_THRESHOLD = 6;
const linkFocus = "outline-none focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background";
const primaryClass = `inline-flex min-h-10 items-center rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/85 ${linkFocus}`;
const secondaryClass = `inline-flex min-h-10 items-center rounded-lg border bg-background px-3 py-2 text-sm font-medium transition-colors hover:bg-muted ${linkFocus}`;

type View = { kind: "local" | "unconfigured" | "connect" | "reconnect" | "install" | "connected"; title: string; text: string; action?: { label: string; href: string; primary: boolean } };

/** The one thing to do next for each state of the connection, so the section never offers two competing buttons. */
function viewOf(status: GitHubStatus, githubMode: boolean): View {
  const install = githubInstallUrl(status.install_url);
  if (!status.configured) return githubMode
    ? { kind: "unconfigured", title: "GitHub App is not configured", text: "Ask the operator to finish GitHub App setup. Public repositories can still be read for coding tasks." }
    : { kind: "local", title: "Signed in with local access", text: "Local mode: public repositories only. Sign in with GitHub (AUTH_MODE=github_app) to use private repositories and open pull requests." };
  if (status.reauthorization_required) return { kind: "reconnect", title: "Reconnect your GitHub account", text: "Your GitHub authorization has expired or was revoked. Reconnect to keep using your repositories.", action: { label: "Reconnect GitHub", href: SIGN_IN, primary: true } };
  if (!status.connected) return { kind: "connect", title: "Connect your GitHub account", text: `Continue with GitHub to choose repositories. ${PUBLISHING}`, action: { label: "Continue with GitHub", href: SIGN_IN, primary: true } };
  if (status.installations === 0) return { kind: "install", title: "Install the GitHub App", text: "Install the app on your account or organization and choose which repositories it may access.", action: install ? { label: "Install the GitHub App", href: install, primary: true } : undefined };
  return { kind: "connected", title: "Connected to GitHub", text: `Your installation controls repository access. ${PUBLISHING}`, action: install ? { label: "Manage repositories", href: install, primary: false } : undefined };
}

export function GitHubConnection({ embedded = false }: { embedded?: boolean }) {
  const identity = useWorkspaceIdentity();
  const [status, setStatus] = useState<GitHubStatus | null>(null);
  const [repositories, setRepositories] = useState<GitHubRepository[]>([]);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  // GitHub sends the person back here with `installed=1` after they install the app: say so once and read the new repositories.
  const [installed] = useState(() => typeof window !== "undefined" && new URLSearchParams(window.location.search).get("installed") === "1");
  const force = useRef(installed);
  useEffect(() => {
    if (!installed) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("installed");
    window.history.replaceState(window.history.state, "", url);
  }, [installed]);
  useEffect(() => {
    const controller = new AbortController();
    const forced = force.current;
    force.current = false;
    void (async () => {
      try {
        const connection = await api<GitHubStatus>("/github/status", { signal: controller.signal });
        let reauthorize = !!connection.reauthorization_required;
        let list: GitHubRepository[] = [];
        if (connection.connected && !reauthorize) {
          try { list = await repositoryCache.get({ force: forced }); }
          catch (failure) { if (failure instanceof ApiError && failure.code === "github_reauthorization_required") reauthorize = true; else throw failure; }
        }
        if (!controller.signal.aborted) { setStatus({ ...connection, reauthorization_required: reauthorize }); setRepositories(list); setError(""); setLoading(false); }
      } catch (failure) { if (!controller.signal.aborted) { setError(message(failure)); setLoading(false); } }
    })();
    return () => controller.abort();
  }, [refresh]);
  const view = status ? viewOf(status, identity?.session.mode === "github_app") : null;
  const needle = query.trim().toLowerCase();
  const visible = useMemo(() => repositories.filter((repo) => !needle || repo.full_name.toLowerCase().includes(needle)), [repositories, needle]);
  return <section className={embedded ? "space-y-6" : "mx-auto w-full max-w-3xl space-y-8 px-5 py-10 sm:px-8"} aria-labelledby="github-title" aria-busy={loading}>
    <SectionHeader id="github-title" title="GitHub" description="Connect repositories and manage their access." action={<Button variant="ghost" size="icon" aria-label="Refresh GitHub connection" disabled={loading} onClick={() => { force.current = true; setLoading(true); setRefresh((value) => value + 1); }}><RefreshCw className={loading ? "animate-spin motion-reduce:animate-none" : ""} /></Button>} />
    {installed ? <p role="status" className="flex items-center gap-2.5 rounded-xl border bg-card px-4 py-3 text-sm"><CircleCheck className="size-4 shrink-0 text-emerald-500" aria-hidden="true" />GitHub App installed — your repositories are ready.</p> : null}
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    {!status && !error ? <div role="status" className={`${cardClass} flex gap-4 p-5 sm:p-6`}><span className="sr-only">Checking your GitHub connection…</span><div className="size-9 shrink-0 animate-pulse rounded-lg bg-muted motion-reduce:animate-none" aria-hidden="true" /><div className="w-full space-y-3" aria-hidden="true"><div className="h-5 w-48 animate-pulse rounded bg-muted motion-reduce:animate-none" /><div className="h-4 w-full max-w-md animate-pulse rounded bg-muted motion-reduce:animate-none" /><div className="h-10 w-40 animate-pulse rounded-lg bg-muted motion-reduce:animate-none" /></div></div> : null}
    {view ? <div className={`${cardClass} flex gap-4 p-5 sm:p-6`} data-github-state={view.kind}>
      <span className={iconTileClass} aria-hidden="true">{view.kind === "connected" ? <CircleCheck className="text-emerald-500" /> : view.kind === "reconnect" || view.kind === "unconfigured" ? <TriangleAlert className="text-amber-500" /> : <GitHubMark />}</span>
      <div className="min-w-0 space-y-4"><div className="space-y-1.5"><p className="text-sm font-medium">{view.title}</p><p className="max-w-prose text-sm leading-6 text-muted-foreground">{view.text}</p></div>{view.action ? <a href={view.action.href} className={view.action.primary ? primaryClass : secondaryClass}>{view.action.label}</a> : null}</div>
    </div> : null}
    {view?.kind === "connected" ? <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-sm font-medium">Available repositories{repositories.length ? <span className="ml-2 rounded-full bg-muted px-1.5 py-0.5 text-xs font-normal tabular-nums text-muted-foreground">{repositories.length}</span> : null}</h3>
        {repositories.length > SEARCH_THRESHOLD ? <div className="relative w-full sm:w-64"><Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" /><input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter repositories" aria-label="Filter repositories" autoCapitalize="none" autoCorrect="off" spellCheck={false} className="h-9 w-full rounded-lg border border-input bg-background pl-8 pr-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40" /></div> : null}</div>
      {repositories.length ? (visible.length ? <ul className={`${cardClass} divide-y`}>{visible.map((repo) => <li key={repo.id} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 px-4 py-3.5 sm:px-5"><div className="min-w-0"><p className="flex items-center gap-1.5 break-all text-sm font-medium">{repo.full_name}{repo.private ? <Lock className="size-3 shrink-0 text-muted-foreground" aria-label="Private repository" /> : null}</p><p className="mt-1 text-xs text-muted-foreground">{repo.private ? "Private" : "Public"} · {repo.default_branch}</p></div><span className="text-xs text-muted-foreground">{repo.can_write ? "Can publish branches and pull requests" : "Read only: can't publish"}</span></li>)}</ul>
        : <p role="status" className="text-sm text-muted-foreground">No repositories match “{query.trim()}”.</p>)
        : <p className="text-sm leading-6 text-muted-foreground">No repositories are available. Grant access in the GitHub installation, then refresh.</p>}
    </div> : null}
  </section>;
}
