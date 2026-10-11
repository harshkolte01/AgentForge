"use client";
import Link from "next/link";
import { useEffect, useId, useMemo, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { ArrowLeft, Check, ChevronDown, FolderGit2, Lock, Plus, RefreshCw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { inputClass } from "@/components/session-boundary";
import { chipClass, iconButtonClass, optionClass } from "@/features/sessions/composer-styles";
import { ApiError, message } from "@/lib/task-api";
import type { GitHubRepository } from "@/lib/github-api";
import { repositoryCache } from "@/lib/github-repositories-cache";
import type { RepositorySelection } from "@/lib/workspace-api";

const SEARCH_THRESHOLD = 6;
const linkClass = "rounded underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/60";
/** Why the list could not be read decides what to offer: reconnecting, connecting, or simply trying again. */
const needsConnection = new Set(["github_authorization_failed", "github_app_user_token_required", "github_access_denied"]);

export function RepositoryPicker({ repository, disabled, onChange, side = "top" }: { repository: RepositorySelection | null; disabled: boolean; onChange: (value: RepositorySelection | null) => Promise<void>; side?: "top" | "bottom" }) {
  const id = useId();
  const [repositories, setRepositories] = useState<GitHubRepository[]>(() => repositoryCache.peek() ?? []);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // The list could not be read: `code` is the server's reason, when it gave one.
  const [unavailable, setUnavailable] = useState<{ code?: string } | null>(null);
  const [open, setOpen] = useState(false);
  const [custom, setCustom] = useState(false);
  const [query, setQuery] = useState("");
  const [name, setName] = useState(repository?.repo_id ?? "");
  const [ref, setRef] = useState(repository?.ref ?? "HEAD");
  const [refresh, setRefresh] = useState(0);
  const search = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    // Shared and cached for a minute: opening the menu again, or another conversation's menu, reuses the same list.
    let cancelled = false;
    void repositoryCache.get().then((value) => {
      if (!cancelled) { setRepositories(value); setUnavailable(null); setLoading(false); }
    }, (failure: unknown) => { if (!cancelled) { setUnavailable({ code: failure instanceof ApiError ? failure.code : undefined }); setLoading(false); } });
    return () => { cancelled = true; };
  }, [refresh, open]);
  useEffect(() => {
    if (!open) return;
    // Coming back from GitHub (after granting a repository) reads the list again, unless it was read moments ago.
    const reload = () => {
      if (document.hidden || repositoryCache.fresh()) return;
      setLoading(true);
      setRefresh((value) => value + 1);
    };
    window.addEventListener("focus", reload);
    return () => window.removeEventListener("focus", reload);
  }, [open]);
  const listed = repositories.some((item) => item.full_name === repository?.repo_id);
  const showSearch = repositories.length > SEARCH_THRESHOLD;
  const needle = query.trim().toLowerCase();
  const visible = useMemo(() => repositories.filter((item) => !needle || item.full_name.toLowerCase().includes(needle)), [repositories, needle]);
  const showCurrentUnlisted = !!repository && !listed && (!needle || repository.repo_id.toLowerCase().includes(needle));
  function changeOpen(next: boolean) {
    if (next && (blocked || busy)) return;
    setOpen(next);
    if (next) { setRepositories((current) => repositoryCache.peek() ?? current); setLoading(!repositoryCache.fresh()); setCustom(false); setQuery(""); setError(""); }
  }
  async function choose(value: RepositorySelection | null) {
    if (busy) return;
    if ((value?.repo_id ?? null) === (repository?.repo_id ?? null) && (value?.ref ?? null) === (repository?.ref ?? null)) { setOpen(false); return; }
    setBusy(true); setError("");
    try { await onChange(value); setOpen(false); setCustom(false); }
    catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }
  function openCustom() { setName(repository?.repo_id ?? ""); setRef(repository?.ref ?? "HEAD"); setError(""); setCustom(true); }
  function applyCustom(event: FormEvent) {
    // The picker renders in a portal; keep this nested submit from reaching the composer form.
    event.preventDefault(); event.stopPropagation();
    if (!/^[A-Za-z0-9-]+\/[A-Za-z0-9_.-]+$/.test(name.trim()) || !ref.trim()) { setError("Enter owner/repository and a branch or commit."); return; }
    void choose({ repo_id: name.trim(), ref: ref.trim() });
  }
  function options() { return Array.from(list.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? []); }
  function navigate(event: KeyboardEvent<HTMLElement>) {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const items = options();
    if (!items.length) return;
    const index = items.indexOf(document.activeElement as HTMLElement);
    let next = -1;
    if (event.key === "Home") next = 0;
    else if (event.key === "End") next = items.length - 1;
    else if (event.key === "ArrowDown") next = index < items.length - 1 ? index + 1 : index;
    else if (index > 0) next = index - 1;
    else if (showSearch && search.current) { event.preventDefault(); search.current.focus(); return; }
    if (next < 0) return;
    event.preventDefault(); items[next].focus();
  }
  const blocked = disabled && !busy;
  const label = repository?.repo_id ?? "No repository";
  const rows: Array<{ key: string; label: string; selected: boolean; private?: boolean; detail?: string; value: RepositorySelection | null }> = [];
  if (!needle) rows.push({ key: "none", label: "No repository", selected: !repository, value: null });
  if (showCurrentUnlisted && repository) rows.push({ key: `current-${repository.repo_id}`, label: repository.repo_id, selected: true, detail: repository.ref === "HEAD" ? "default branch" : repository.ref, value: repository });
  for (const item of visible) {
    const selected = item.full_name === repository?.repo_id;
    rows.push({ key: String(item.id), label: item.full_name, selected, private: item.private, detail: selected && repository ? (repository.ref === "HEAD" ? "default branch" : repository.ref) : undefined, value: selected && repository ? repository : { repo_id: item.full_name, ref: item.default_branch } });
  }
  return <Popover open={open} onOpenChange={changeOpen}>
    <PopoverTrigger disabled={blocked} aria-label={`Conversation repository: ${label}`} title={repository ? `${repository.repo_id} · ${repository.ref === "HEAD" ? "default branch" : repository.ref}` : "Work without a repository"} className={`${chipClass} ${repository ? "text-foreground" : ""}`}>
      <FolderGit2 className="size-4" aria-hidden="true" />
      <span className="min-w-0 truncate">{label}</span>
      <ChevronDown className="size-3 opacity-60 max-[359px]:hidden" aria-hidden="true" />
    </PopoverTrigger>
    <PopoverContent side={side} align="start" className="w-[min(22rem,calc(100vw-1.5rem))]" initialFocus={(type) => type === "touch" ? true : custom ? true : showSearch ? search.current : list.current?.querySelector<HTMLElement>('[aria-selected="true"]') ?? true}>
      {custom ? <form onSubmit={applyCustom} className="space-y-3 p-3">
        <div className="flex items-center gap-1"><button type="button" className={iconButtonClass} aria-label="Back to repositories" onClick={() => { setCustom(false); setError(""); }} disabled={busy}><ArrowLeft className="size-4" /></button><h2 className="text-[13px] font-medium">Use another repository</h2></div>
        <div className="space-y-1"><label htmlFor={`${id}-name`} className="text-xs font-medium">Repository</label><input id={`${id}-name`} autoFocus value={name} onChange={(event) => setName(event.target.value)} placeholder="owner/repository" maxLength={140} autoCapitalize="none" autoCorrect="off" spellCheck={false} disabled={blocked} readOnly={busy} className={inputClass} /></div>
        <div className="space-y-1"><label htmlFor={`${id}-ref`} className="text-xs font-medium">Branch or commit</label><input id={`${id}-ref`} placeholder="HEAD (default branch)" value={ref} onChange={(event) => setRef(event.target.value)} maxLength={200} autoCapitalize="none" autoCorrect="off" spellCheck={false} disabled={blocked} readOnly={busy} className={inputClass} /></div>
        {error ? <p role="alert" className="text-xs leading-5 text-destructive">{error}</p> : null}
        <div className="flex flex-wrap items-center justify-between gap-2"><Link href="/settings?section=github" className="text-xs text-muted-foreground underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/60">Manage GitHub access</Link><Button type="submit" size="sm" className="min-h-10 px-3 sm:min-h-8" disabled={blocked} aria-disabled={busy || undefined}>{busy ? "Saving…" : "Use repository"}</Button></div>
      </form> : <div className="flex flex-col p-1.5" onKeyDown={navigate}>
        {showSearch ? <div className="relative mb-1"><Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" /><input ref={search} type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search repositories" aria-label="Search repositories" autoCapitalize="none" autoCorrect="off" spellCheck={false} className="h-10 w-full rounded-lg bg-muted/60 pl-8 pr-2 text-[13px] outline-none placeholder:text-muted-foreground focus-visible:ring-1 focus-visible:ring-ring sm:h-8" /></div> : null}
        <div ref={list} role="listbox" aria-label="Repositories" className="flex max-h-64 flex-col gap-px overflow-y-auto overscroll-contain">
          {rows.map((row) => <button key={row.key} type="button" role="option" aria-selected={row.selected} aria-disabled={busy || undefined} tabIndex={-1} className={optionClass} onClick={() => void choose(row.value)}>
            <span className="min-w-0 flex-1 truncate">{row.label}</span>
            {row.private ? <Lock className="size-3 shrink-0 text-muted-foreground" aria-label="Private repository" /> : null}
            {row.detail ? <span className="max-w-24 shrink-0 truncate text-xs text-muted-foreground">{row.detail}</span> : null}
            <Check className={`size-4 shrink-0 ${row.selected ? "" : "invisible"}`} aria-hidden="true" />
          </button>)}
          {needle && !rows.length ? <p className="px-2.5 py-3 text-[13px] text-muted-foreground" role="status">No matching repositories.</p> : null}
        </div>
        {loading ? <p className="px-2.5 py-2 text-xs text-muted-foreground" role="status">Loading your repositories…</p> : null}
        {!loading && unavailable ? <p className="flex flex-wrap items-center gap-x-2 px-2.5 py-2 text-xs leading-5 text-muted-foreground" role="status">
          {unavailable.code === "github_reauthorization_required" ? <>Your GitHub authorization has expired.<a href="/api/v1/auth/github/start" className={linkClass}>Reconnect GitHub</a></>
            : unavailable.code === "github_auth_not_configured" ? <>GitHub isn’t set up here, so only public repositories can be used.<Link href="/settings?section=github" className={linkClass}>GitHub settings</Link></>
              : unavailable.code && needsConnection.has(unavailable.code) ? <>Connect GitHub to list your repositories.<Link href="/settings?section=github" className={linkClass}>Connect GitHub</Link></>
                : <>Your repositories couldn’t be loaded.<button type="button" className={`inline-flex items-center gap-1 ${linkClass}`} onClick={() => { repositoryCache.invalidate(); setLoading(true); setRefresh((value) => value + 1); }}><RefreshCw className="size-3" aria-hidden="true" />Retry</button></>}
        </p> : null}
        {!loading && !unavailable && !repositories.length ? <p className="px-2.5 py-2 text-xs leading-5 text-muted-foreground">No connected repositories yet. <Link href="/settings?section=github" className={linkClass}>Manage GitHub access</Link></p> : null}
        {error ? <p role="alert" className="px-2.5 py-2 text-xs leading-5 text-destructive">{error}</p> : null}
        <div className="mt-1 border-t pt-1"><button type="button" className={optionClass} onClick={openCustom}><Plus className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" /><span className="flex-1">Use another repository…</span></button></div>
      </div>}
    </PopoverContent>
  </Popover>;
}
