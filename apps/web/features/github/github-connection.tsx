"use client";
import { useEffect, useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, message } from "@/lib/task-api";
import { githubInstallUrl, type GitHubRepository, type GitHubStatus } from "@/lib/github-api";

export function GitHubConnection({ embedded = false }: { embedded?: boolean }) {
  const [status, setStatus] = useState<GitHubStatus | null>(null);
  const [repositories, setRepositories] = useState<GitHubRepository[]>([]);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const connection = await api<GitHubStatus>("/github/status", { signal: controller.signal });
        const data = connection.connected ? await api<{ repositories: GitHubRepository[] }>("/github/repositories", { signal: controller.signal }) : { repositories: [] };
        if (!controller.signal.aborted) { setStatus(connection); setRepositories(data.repositories); setError(""); setLoading(false); }
      } catch (error) { if (!controller.signal.aborted) { setError(message(error)); setLoading(false); } }
    })();
    return () => controller.abort();
  }, [refresh]);
  const install = githubInstallUrl(status?.install_url);
  return <section className={embedded ? "space-y-6" : "mx-auto w-full max-w-3xl space-y-8 px-5 py-10 sm:px-8"} aria-labelledby="github-title">
    <header className="flex items-start justify-between gap-4"><div className="space-y-2"><h2 id="github-title" className="text-lg font-semibold">GitHub connection</h2><p className="max-w-prose text-sm leading-6 text-muted-foreground">Connect repositories and manage their access.</p></div><Button variant="ghost" size="icon" aria-label="Refresh GitHub connection" disabled={loading} onClick={() => { setLoading(true); setRefresh((value) => value + 1); }}><RefreshCw /></Button></header>
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    {!status && !error ? <p role="status" className="text-sm text-muted-foreground">Checking connection…</p> : null}
    {status ? <div className="space-y-4 border-y py-5"><p className="text-sm font-medium">{status.connected ? "Connected to GitHub" : status.configured ? "Connect your repositories" : "GitHub App is not configured"}</p><p className="text-sm leading-6 text-muted-foreground">{status.configured ? "Your installation controls repository access. Publishing always requires approval of the exact changes." : "Ask the operator to finish GitHub App setup. Public repository reads may still be available for coding tasks."}</p>{status.configured && !status.connected ? <a href="/api/v1/auth/github/start" className="mr-3 inline-flex min-h-10 items-center rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/85 focus-visible:outline-2 focus-visible:outline-offset-2">Connect GitHub account</a> : null}{install ? <a href={install} className="inline-flex min-h-10 items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium hover:bg-muted focus-visible:outline-2 focus-visible:outline-offset-2" target="_blank" rel="noopener noreferrer">{status.connected ? "Manage repository access" : "Connect GitHub App"}<ExternalLink className="size-4" aria-hidden="true" /></a> : null}</div> : null}
    {status?.connected ? <div className="space-y-4"><h2 className="text-sm font-semibold">Available repositories</h2>{repositories.length ? <ul className="divide-y">{repositories.map((repo) => <li key={repo.id} className="flex flex-wrap items-center justify-between gap-2 py-4"><div><p className="break-all text-sm font-medium">{repo.full_name}</p><p className="mt-1 text-xs text-muted-foreground">{repo.private ? "Private" : "Public"} · {repo.default_branch}</p></div><span className="text-xs text-muted-foreground">{repo.can_write ? "Can request publishing" : "Read access"}</span></li>)}</ul> : <p className="text-sm text-muted-foreground">No repositories are available. Grant access in the GitHub installation, then refresh.</p>}</div> : null}
  </section>;
}
