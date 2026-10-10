"use client";
import { useEffect, useState, type FormEvent } from "react";
import { Archive, ArchiveRestore, Check, MessageSquare, MoreHorizontal, Pencil, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/session-boundary";
import { api, message } from "@/lib/task-api";
import type { ConversationSession } from "@/lib/workspace-api";

export function SessionHistory({ sessions, selectedId, loading, disabled, onSelect, onChange }: { disabled: boolean; sessions: ConversationSession[]; selectedId: string | null; loading: boolean; onSelect: (session: ConversationSession) => void; onChange: (session: ConversationSession) => void }) {
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [results, setResults] = useState<ConversationSession[] | null>(null);
  const [offset, setOffset] = useState(0);
  const [more, setMore] = useState(false);
  const [searching, setSearching] = useState(false);
  const [menuPosition, setMenuPosition] = useState({ top: 0, left: 0 });
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearching(true);
      void api<ConversationSession[]>(`/sessions?archived=${archived}&q=${encodeURIComponent(query)}&limit=50&offset=${offset}`, { signal: controller.signal }).then((batch) => {
        if (!controller.signal.aborted) { setResults((current) => offset ? [...new Map([...(current ?? []), ...batch].map((item) => [item.id, item])).values()] : batch); setMore(batch.length === 50); setSearching(false); setError(""); }
      }).catch((error) => { if (!controller.signal.aborted) { setError(message(error)); setSearching(false); } });
    }, query ? 250 : 0);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [query, archived, offset, sessions]);
  const visible = (results ?? sessions).filter((session) => session.archived === archived && session.title.toLowerCase().includes(query.toLowerCase()));
  async function update(session: ConversationSession, patch: { title?: string; archived?: boolean }) {
    setBusy(session.id); setError("");
    try { const result = await api<ConversationSession>(`/sessions/${session.id}`, { method: "PATCH", body: JSON.stringify(patch) }); onChange(result); setEditing(null); }
    catch (error) { setError(message(error)); } finally { setBusy(null); }
  }
  function rename(event: FormEvent, session: ConversationSession) { event.preventDefault(); void update(session, { title: title.trim() }); }
  return <section className="flex min-h-0 flex-1 flex-col" aria-label="Conversation history">
    <div className="relative mb-4"><Search className="pointer-events-none absolute left-2.5 top-3.5 sm:top-2.5 size-3.5 text-muted-foreground" aria-hidden="true" /><label htmlFor="history-search" className="sr-only">Search conversation history</label><input id="history-search" className={`${inputClass} min-h-11 border-transparent bg-muted/60 py-2 pl-8 text-xs focus-visible:bg-background sm:min-h-9`} placeholder="Search conversations" value={query} maxLength={120} onChange={(event) => { setQuery(event.target.value); setResults(null); setOffset(0); }} /></div>
    <div className="mb-2 flex items-center justify-between px-2"><h2 className="text-xs font-medium text-muted-foreground">{archived ? "Archived" : "Conversations"}</h2><Button variant="ghost" size="icon-xs" className="size-11 sm:size-8" aria-label={archived ? "Show active conversations" : "Show archived conversations"} aria-pressed={archived} onClick={() => { setArchived((value) => !value); setResults(null); setOffset(0); }}>{archived ? <ArchiveRestore /> : <Archive />}</Button></div>
    {error ? <p role="alert" className="px-2 py-2 text-xs leading-5 text-destructive">{error}</p> : null}
    <ul className="min-h-0 space-y-1 overflow-y-auto overscroll-contain pb-5">
      {visible.map((session) => <li key={session.id} className={`rounded-lg ${session.id === selectedId ? "bg-sidebar-accent" : "hover:bg-sidebar-accent/60"}`}>
        {editing === session.id ? <form onSubmit={(event) => rename(event, session)} className="space-y-2 p-2"><label className="sr-only" htmlFor={`rename-${session.id}`}>Conversation title</label><input id={`rename-${session.id}`} value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={120} className={inputClass} autoFocus disabled={busy === session.id} /><div className="flex justify-end gap-1"><Button type="button" variant="ghost" size="icon-sm" aria-label="Cancel rename" onClick={() => setEditing(null)}><X /></Button><Button type="submit" variant="ghost" size="icon-sm" aria-label="Save conversation title" disabled={busy === session.id || !title.trim()}><Check /></Button></div></form> : <div className="flex items-center gap-0.5 pl-2">
          <button className="flex min-h-11 min-w-0 flex-1 sm:min-h-9 items-center gap-2 rounded-md py-1.5 pr-1 text-left text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-current={session.id === selectedId ? "page" : undefined} disabled={disabled} onClick={() => onSelect(session)}><MessageSquare className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" /><span className="truncate">{session.title}</span>{session.active_task_id ? <span className="size-1.5 shrink-0 rounded-full bg-foreground" aria-label="Task active" /> : null}</button>
          <div className="pr-1"><button type="button" popoverTarget={`history-actions-${session.id}`} className="flex size-11 items-center justify-center rounded-md text-muted-foreground hover:bg-muted focus-visible:outline-2 disabled:opacity-50 sm:size-8" aria-label={`Actions for ${session.title}`} disabled={disabled || !!busy} onClick={(event) => { const rect = event.currentTarget.getBoundingClientRect(); setMenuPosition({ top: Math.min(rect.bottom + 4, window.innerHeight - 110), left: Math.max(8, rect.right - 160) }); }}><MoreHorizontal className="size-3.5" aria-hidden="true" /></button><div id={`history-actions-${session.id}`} popover="auto" style={{ position: "fixed", top: menuPosition.top, left: menuPosition.left, margin: 0 }} className="w-40 rounded-lg border bg-popover p-1 text-popover-foreground shadow-md"><button type="button" className="flex min-h-11 w-full items-center gap-2 rounded-md px-2 text-xs hover:bg-muted focus-visible:outline-2 sm:min-h-9" onClick={() => { document.getElementById(`history-actions-${session.id}`)?.hidePopover(); setEditing(session.id); setTitle(session.title); }}><Pencil className="size-3.5" aria-hidden="true" />Rename</button><button type="button" className="flex min-h-11 w-full items-center gap-2 rounded-md px-2 text-xs hover:bg-muted focus-visible:outline-2 disabled:opacity-50 sm:min-h-9" disabled={!!busy || !!session.active_task_id} onClick={() => { document.getElementById(`history-actions-${session.id}`)?.hidePopover(); void update(session, { archived: !session.archived }); }}>{session.archived ? <ArchiveRestore className="size-3.5" /> : <Archive className="size-3.5" />}{session.archived ? "Restore" : "Archive"}</button>{session.active_task_id ? <p className="px-2 py-1 text-xs text-muted-foreground">Finish the active turn before archiving.</p> : null}</div></div>
        </div>}
      </li>)}
    </ul>
    {more ? <Button variant="ghost" size="sm" disabled={searching} onClick={() => setOffset((value) => value + 50)}>{searching ? "Loading…" : "Load more conversations"}</Button> : null}
    {!visible.length ? <p role="status" className="px-2 py-5 text-xs leading-5 text-muted-foreground">{loading || searching ? "Loading conversations…" : query ? "No matching conversations." : archived ? "No archived conversations." : "Your conversations will appear here."}</p> : null}
  </section>;
}
