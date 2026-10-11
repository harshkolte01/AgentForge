"use client";
import Link from "next/link";
import { HistoryTitle } from "./history-title";
import "./session-history.css";
import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Archive, ArchiveRestore, Check, MoreHorizontal, Pencil, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/session-boundary";
import { SessionMark } from "@/features/sessions/session-mark";
import { conversationMark } from "@/lib/conversation-mark";
import { mergeSessions } from "@/lib/conversation-order";
import { isLive, sidebarStatus, statusAnnouncement, type SidebarStatus } from "@/lib/conversation-status";
import { api, message } from "@/lib/task-api";
import { routes } from "@/lib/routes";
import { usePageVisible } from "@/lib/use-page-visible";
import type { ConversationSession } from "@/lib/workspace-api";

/** How often the list is read again: quickly while some conversation is working or waiting for you, slowly otherwise. */
const LIVE_CHECK_MS = 4000;
const IDLE_CHECK_MS = 30000;

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
  }, [query, archived, offset]);
  // The list read here and the workspace's own copies (a conversation just sent to, renamed or opened) are merged, newest copy
  // of each winning, so a change made elsewhere shows at once without reading the list again.
  const visible = useMemo(() => (results ? mergeSessions(results, sessions) : sessions).filter((session) => session.archived === archived && session.title.toLowerCase().includes(query.toLowerCase())), [results, sessions, archived, query]);
  const pageVisible = usePageVisible();
  const live = visible.some(isLive);
  // Conversations change while you are elsewhere (an answer arrives, a tool waits for you), so the list is read again on its
  // own. Only `results` is updated here, never the parent's `sessions`, which would read the list a second time.
  useEffect(() => {
    if (query) return; // a search shows what matched, not the live list
    let stopped = false;
    let generation = 0;
    let timer: number | undefined;
    let controller: AbortController | null = null;
    const read = async () => {
      if (document.hidden) return;
      controller?.abort();
      const mine = (controller = new AbortController());
      try {
        const batch = await api<ConversationSession[]>(`/sessions?archived=${archived}&limit=50`, { signal: mine.signal });
        if (!mine.signal.aborted) setResults((current) => (current ? mergeSessions(current, batch) : current));
      } catch { /* the next check tries again; a missed check is not worth an error banner */ }
    };
    // One chain of checks at a time: waking up (tab shown, window focused) restarts it instead of adding a second.
    const loop = async () => {
      const mine = ++generation;
      await read();
      if (!stopped && mine === generation) timer = window.setTimeout(() => void loop(), live ? LIVE_CHECK_MS : IDLE_CHECK_MS);
    };
    const wake = () => { if (document.hidden || stopped) return; window.clearTimeout(timer); void loop(); };
    timer = window.setTimeout(() => void loop(), live ? LIVE_CHECK_MS : IDLE_CHECK_MS);
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("focus", wake);
    return () => { stopped = true; controller?.abort(); window.clearTimeout(timer); document.removeEventListener("visibilitychange", wake); window.removeEventListener("focus", wake); };
  }, [query, archived, live]);
  // A screen reader hears when a conversation you are not looking at finishes, fails or needs you.
  const shown = useRef(new Map<string, SidebarStatus | null>());
  const announcer = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    const next = new Map<string, SidebarStatus | null>();
    let said: string | null = null;
    for (const session of visible) {
      const status = sidebarStatus(session, session.id === selectedId && pageVisible);
      next.set(session.id, status);
      said = statusAnnouncement(session.title, shown.current.get(session.id), status) ?? said;
    }
    shown.current = next;
    if (said && announcer.current) announcer.current.textContent = said;
  }, [visible, selectedId, pageVisible]);
  async function update(session: ConversationSession, patch: { title?: string; archived?: boolean }) {
    setBusy(session.id); setError("");
    try { const result = await api<ConversationSession>(`/sessions/${session.id}`, { method: "PATCH", body: JSON.stringify(patch) }); onChange(result); setEditing(null); }
    catch (error) { setError(message(error)); } finally { setBusy(null); }
  }
  function rename(event: FormEvent, session: ConversationSession) { event.preventDefault(); void update(session, { title: title.trim() }); }
  return <section className="flex min-h-0 flex-1 flex-col" aria-label="Conversation history">
    <div className="relative mb-5"><Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" aria-hidden="true" /><label htmlFor="history-search" className="sr-only">Search conversation history</label><input id="history-search" className="min-h-11 w-full rounded-lg border border-transparent bg-muted/60 py-2 pl-9 pr-9 text-xs outline-none placeholder:text-muted-foreground transition-colors focus:border-border focus:bg-background focus:ring-2 focus:ring-ring/20 motion-reduce:transition-none sm:min-h-9" placeholder="Search conversations" value={query} maxLength={120} onChange={(event) => { setQuery(event.target.value); setResults(null); setOffset(0); }} />{query ? <button type="button" aria-label="Clear conversation search" className="absolute right-0 top-0 flex h-full w-9 items-center justify-center rounded-md text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60" onClick={() => { setQuery(""); setResults(null); setOffset(0); document.getElementById("history-search")?.focus(); }}><X className="size-3.5" aria-hidden="true" /></button> : null}</div>
    <div className="mb-2 flex items-center justify-between px-2"><h2 className="text-xs font-medium text-muted-foreground">{archived ? "Archived" : "Conversations"}</h2><Button variant="ghost" size="icon-xs" className="size-11 sm:size-8" aria-label={archived ? "Show active conversations" : "Show archived conversations"} aria-pressed={archived} onClick={() => { setArchived((value) => !value); setResults(null); setOffset(0); }}>{archived ? <ArchiveRestore /> : <Archive />}</Button></div>
    {error ? <p role="alert" className="px-2 py-2 text-xs leading-5 text-destructive">{error}</p> : null}
    <ul className={`history-list min-h-0 space-y-0.5 overflow-y-auto overscroll-contain px-0.5 pb-4`} aria-busy={loading || searching}>
      {visible.map((session) => { const status = sidebarStatus(session, session.id === selectedId && pageVisible); const mark = conversationMark(session, session.id === selectedId && pageVisible); return <li key={session.id} data-status={status ?? undefined} className={`history-row rounded-lg ${session.id === selectedId ? "bg-sidebar-accent text-sidebar-accent-foreground" : "hover:bg-sidebar-accent/60"}`}>
        {editing === session.id ? <form onSubmit={(event) => rename(event, session)} onKeyDown={(event) => { if (event.key === "Escape" && !busy) { event.preventDefault(); event.stopPropagation(); setEditing(null); requestAnimationFrame(() => document.getElementById(`history-trigger-${session.id}`)?.focus()); } }} className="space-y-2 p-2"><label className="sr-only" htmlFor={`rename-${session.id}`}>Conversation title</label><input id={`rename-${session.id}`} value={title} onChange={(event) => setTitle(event.target.value)} required maxLength={120} className={inputClass} autoFocus disabled={busy === session.id} /><div className="flex justify-end gap-1"><Button type="button" variant="ghost" size="icon-sm" aria-label="Cancel rename" disabled={busy === session.id} onClick={() => { setEditing(null); requestAnimationFrame(() => document.getElementById(`history-trigger-${session.id}`)?.focus()); }}><X /></Button><Button type="submit" variant="ghost" size="icon-sm" aria-label="Save conversation title" disabled={busy === session.id || !title.trim()}><Check /></Button></div></form> : <div className="flex items-center gap-0.5 pl-2">
          <Link href={routes.conversation(session.id)} title={mark.notable ? `${session.title} · ${mark.label}` : session.title} prefetch={false} className="history-link flex min-h-11 min-w-0 flex-1 sm:min-h-10 items-center gap-2.5 rounded-md py-1.5 pr-1 text-left text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring aria-disabled:pointer-events-none aria-disabled:opacity-60" aria-current={session.id === selectedId ? "page" : undefined} aria-disabled={disabled || undefined} onClick={(event) => {
            if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
            if (disabled) { event.preventDefault(); return; }
            onSelect(session);
          }}><SessionMark mark={mark} /><HistoryTitle title={session.title} /></Link>
          <div className={`history-actions shrink-0 pr-1`}><button id={`history-trigger-${session.id}`} type="button" popoverTarget={`history-actions-${session.id}`} className="flex size-11 items-center justify-center rounded-md text-muted-foreground outline-none hover:bg-background hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 disabled:opacity-50 sm:size-7" aria-label={`Actions for ${session.title}`} disabled={disabled || !!busy} onClick={(event) => { const rect = event.currentTarget.getBoundingClientRect(); setMenuPosition({ top: Math.min(rect.bottom + 4, window.innerHeight - 180), left: Math.max(8, rect.right - 160) }); }}><MoreHorizontal className="size-3.5" aria-hidden="true" /></button><div id={`history-actions-${session.id}`} popover="auto" style={{ position: "fixed", top: menuPosition.top, left: menuPosition.left, margin: 0 }} className="max-h-[calc(100dvh-1rem)] w-40 overflow-y-auto rounded-lg border bg-popover p-1 text-popover-foreground shadow-md"><button type="button" className="flex min-h-11 w-full items-center gap-2 rounded-md px-2 text-xs outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/60 sm:min-h-9" onClick={() => { document.getElementById(`history-actions-${session.id}`)?.hidePopover(); setEditing(session.id); setTitle(session.title); }}><Pencil className="size-3.5" aria-hidden="true" />Rename</button><button type="button" className="flex min-h-11 w-full items-center gap-2 rounded-md px-2 text-xs outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/60 disabled:opacity-50 sm:min-h-9" disabled={!!busy || !!session.active_task_id || !!session.queued_count} onClick={() => { document.getElementById(`history-actions-${session.id}`)?.hidePopover(); void update(session, { archived: !session.archived }); }}>{session.archived ? <ArchiveRestore className="size-3.5" /> : <Archive className="size-3.5" />}{session.archived ? "Restore" : "Archive"}</button>{session.active_task_id ? <p className="px-2 py-1 text-xs text-muted-foreground">Finish the active turn before archiving.</p> : null}</div></div>
        </div>}
      </li>; })}
    </ul>
    <p ref={announcer} role="status" className="sr-only" />
    {more ? <Button variant="ghost" size="sm" disabled={searching} onClick={() => setOffset((value) => value + 50)}>{searching ? "Loading…" : "Load more conversations"}</Button> : null}
    {!visible.length ? <p role="status" className="px-2 py-5 text-xs leading-5 text-muted-foreground">{loading || searching ? "Loading conversations…" : query ? "No matching conversations." : archived ? "No archived conversations." : "Your conversations will appear here."}</p> : null}
  </section>;
}
