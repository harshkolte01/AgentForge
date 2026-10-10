"use client";
import Link from "next/link";
import { useEffect, useId, useState, type FormEvent } from "react";
import { GitBranch, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/session-boundary";
import { api, message } from "@/lib/task-api";
import type { GitHubRepository } from "@/lib/github-api";
import type { RepositorySelection } from "@/lib/workspace-api";

export function RepositoryPicker({ repository, disabled, onChange }: { repository: RepositorySelection | null; disabled: boolean; onChange: (value: RepositorySelection | null) => Promise<void> }) {
  const id = useId();
  const [repositories, setRepositories] = useState<GitHubRepository[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [unavailable, setUnavailable] = useState(false);
  const [custom, setCustom] = useState(false);
  const [name, setName] = useState(repository?.repo_id ?? "");
  const [ref, setRef] = useState(repository?.ref ?? "HEAD");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void api<{ repositories: GitHubRepository[] }>("/github/repositories", { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) { setRepositories(value.repositories); setUnavailable(false); setLoading(false); }
    }).catch(() => { if (!controller.signal.aborted) { setUnavailable(true); setLoading(false); } });
    return () => controller.abort();
  }, [refresh]);
  async function choose(value: RepositorySelection | null) {
    setBusy(true); setError("");
    try { await onChange(value); setCustom(false); }
    catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }
  function applyCustom(event: FormEvent) {
    event.preventDefault(); event.stopPropagation();
    if (!/^[A-Za-z0-9-]+\/[A-Za-z0-9_.-]+$/.test(name.trim()) || !ref.trim()) { setError("Enter owner/repository and a branch or commit."); return; }
    void choose({ repo_id: name.trim(), ref: ref.trim() });
  }
  const listed = repositories.some((item) => item.full_name === repository?.repo_id);
  return <div className="contents">
    <div className="relative col-start-2 row-start-1 flex min-w-0 items-center gap-1 text-muted-foreground"><GitBranch className="pointer-events-none absolute left-2.5 size-3.5" aria-hidden="true" /><label className="sr-only" htmlFor={id}>Conversation repository</label><select id={id} disabled={disabled || busy} value={repository?.repo_id ?? ""} title={repository ? `${repository.repo_id} · ${repository.ref}` : "No repository"} className="h-11 w-full max-w-full truncate rounded-lg bg-transparent pl-8 pr-6 text-xs outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 sm:h-8" onChange={(event) => {
      if (event.target.value === "__custom") { setName(repository?.repo_id ?? ""); setRef(repository?.ref ?? "HEAD"); setCustom(true); return; }
      const item = repositories.find((item) => item.full_name === event.target.value);
      void choose(item ? { repo_id: item.full_name, ref: item.default_branch } : null);
    }}><option value="">No repository</option>{repository && !listed ? <option value={repository.repo_id}>{repository.repo_id}</option> : null}{repositories.map((item) => <option key={item.id} value={item.full_name}>{item.full_name}{item.private ? " · private" : ""}</option>)}<option value="__custom">Enter repository…</option></select>{busy ? <span className="sr-only" role="status">Saving repository…</span> : null}</div>
    {custom ? <div onKeyDown={(event) => { if (event.target instanceof HTMLInputElement && event.key === "Enter" && !event.nativeEvent.isComposing && event.keyCode !== 229) applyCustom(event); }} className="col-span-3 mt-2 space-y-3 rounded-lg bg-muted/40 p-3"><div className="grid gap-3 sm:grid-cols-2"><div className="space-y-1"><label htmlFor={`${id}-name`} className="text-xs font-medium">Repository</label><input id={`${id}-name`} value={name} onChange={(event) => setName(event.target.value)} placeholder="owner/repository" maxLength={140} autoCapitalize="none" spellCheck={false} disabled={disabled || busy} className={inputClass} /></div><div className="space-y-1"><label htmlFor={`${id}-ref`} className="text-xs font-medium">Branch or commit</label><input id={`${id}-ref`} placeholder="HEAD (default branch)" value={ref} onChange={(event) => setRef(event.target.value)} maxLength={200} autoCapitalize="none" spellCheck={false} disabled={disabled || busy} className={inputClass} /></div></div><div className="flex flex-wrap items-center justify-between gap-2"><Link href="/settings?section=github" className="text-xs text-muted-foreground underline underline-offset-4">Manage GitHub access</Link><div className="flex flex-wrap gap-1"><Button type="button" variant="ghost" size="sm" className="min-h-11 sm:min-h-8" onClick={() => setCustom(false)} disabled={busy}>Cancel</Button><Button type="button" variant="outline" size="sm" className="min-h-11 sm:min-h-8" disabled={disabled || busy} onClick={applyCustom}>{busy ? "Saving…" : "Use repository"}</Button></div></div></div> : null}
    {unavailable && custom ? <p className="col-span-3 mt-2 text-xs leading-5 text-muted-foreground">Repository list is unavailable. Enter a repository above or <button type="button" className="inline-flex items-center gap-1 rounded underline underline-offset-4 focus-visible:outline-2" onClick={() => { setLoading(true); setRefresh((value) => value + 1); }} disabled={loading}><RefreshCw className="size-3" aria-hidden="true" />retry</button>.</p> : null}
    {error ? <p role="alert" className="col-span-3 mt-2 text-xs leading-5 text-destructive">{error}</p> : null}
  </div>;
}
