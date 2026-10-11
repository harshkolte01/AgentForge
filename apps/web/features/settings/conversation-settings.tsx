"use client";
import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { ArchiveRestore, Check, Pencil, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/session-boundary";
import { focusClass, sectionClass, SectionHeader } from "@/features/settings/settings-ui";
import { routes } from "@/lib/routes";
import { api, formatDate, message } from "@/lib/task-api";
import type { ConversationSession } from "@/lib/workspace-api";

/** Archived conversations: open one, rename it, or restore it to the sidebar. */
export function ConversationSettings() {
  const [sessions, setSessions] = useState<ConversationSession[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void api<ConversationSession[]>("/sessions?archived=true&limit=50", { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) { setSessions(value.filter((item) => item.archived)); setError(""); }
    }).catch((failure) => { if (!controller.signal.aborted) { setError(message(failure)); setSessions([]); } });
    return () => controller.abort();
  }, []);
  async function update(session: ConversationSession, patch: { title?: string; archived?: boolean }) {
    setBusy(session.id); setError("");
    try {
      const result = await api<ConversationSession>(`/sessions/${session.id}`, { method: "PATCH", body: JSON.stringify(patch) });
      setSessions((current) => (current ?? []).flatMap((item) => item.id !== result.id ? [item] : result.archived ? [result] : []));
      setEditing(null);
    } catch (failure) { setError(message(failure)); } finally { setBusy(null); }
  }
  function rename(event: FormEvent, session: ConversationSession) { event.preventDefault(); if (title.trim()) void update(session, { title: title.trim() }); }
  return <section className={sectionClass} aria-labelledby="conversations-title">
    <SectionHeader id="conversations-title" title="Conversations" description="Archived conversations are kept here, out of the sidebar. Restore one to continue it." />
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    {!sessions ? <div role="status" className="divide-y border-y"><span className="sr-only">Loading archived conversations…</span>{[0, 1, 2].map((index) => <div key={index} className="flex min-h-14 items-center justify-between gap-4 py-2" aria-hidden="true"><div className="space-y-1.5"><div className="h-4 w-48 animate-pulse rounded bg-muted motion-reduce:animate-none" /><div className="h-3 w-24 animate-pulse rounded bg-muted motion-reduce:animate-none" /></div><div className="h-7 w-20 animate-pulse rounded-lg bg-muted motion-reduce:animate-none" /></div>)}</div>
      : !sessions.length ? <p className="border-y py-5 text-sm text-muted-foreground">No archived conversations.</p>
        : <ul className="divide-y border-y" aria-label="Archived conversations">{sessions.map((session) => <li key={session.id} className="flex min-h-14 flex-wrap items-center justify-between gap-x-4 gap-y-2 py-2">
          {editing === session.id ? <form onSubmit={(event) => rename(event, session)} onKeyDown={(event) => { if (event.key === "Escape" && !busy) { event.preventDefault(); setEditing(null); } }} className="flex w-full items-center gap-1">
            <label htmlFor={`archived-rename-${session.id}`} className="sr-only">Conversation title</label>
            <input id={`archived-rename-${session.id}`} value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={120} autoFocus disabled={busy === session.id} className={inputClass} />
            <Button type="button" variant="ghost" size="icon-sm" aria-label="Cancel rename" disabled={busy === session.id} onClick={() => setEditing(null)}><X /></Button>
            <Button type="submit" variant="ghost" size="icon-sm" aria-label="Save conversation title" disabled={busy === session.id || !title.trim()}><Check /></Button>
          </form> : <>
            <div className="min-w-0 flex-1"><Link href={routes.conversation(session.id)} className={`block truncate rounded text-sm font-medium hover:underline hover:underline-offset-4 ${focusClass}`}>{session.title}</Link><p className="text-xs text-muted-foreground">Updated {formatDate(session.updated_at)}</p></div>
            <div className="flex shrink-0 items-center gap-1">
              <Button variant="ghost" size="sm" disabled={!!busy} onClick={() => { setEditing(session.id); setTitle(session.title); }} aria-label={`Rename ${session.title}`}><Pencil />Rename</Button>
              <Button variant="outline" size="sm" disabled={!!busy} onClick={() => void update(session, { archived: false })} aria-label={`Restore ${session.title}`}><ArchiveRestore />{busy === session.id ? "Restoring…" : "Restore"}</Button>
            </div>
          </>}
        </li>)}</ul>}
  </section>;
}
