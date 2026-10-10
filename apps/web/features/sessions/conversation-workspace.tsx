"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Menu, PanelLeftClose, Settings, SquarePen } from "lucide-react";
import { AccountControl } from "@/components/session-boundary";
import { Button } from "@/components/ui/button";
import { SessionHistory } from "@/features/sessions/session-history";
import { ConversationComposer } from "@/features/sessions/conversation-composer";
import { ConversationThread } from "@/features/sessions/conversation-thread";
import { api, message } from "@/lib/task-api";
import type { ConversationSession, RepositorySelection, TurnInput, TurnResult, WorkspaceConfiguration } from "@/lib/workspace-api";

export function ConversationWorkspace() {
  const [sessions, setSessions] = useState<ConversationSession[]>([]);
  const [selected, setSelected] = useState<ConversationSession | null>(null);
  const [draftSequence, setDraftSequence] = useState(0);
  const [draftRepository, setDraftRepository] = useState<RepositorySelection | null>(null);
  const [config, setConfig] = useState<WorkspaceConfiguration | null>(null);
  const [sidebar, setSidebar] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [threadRefresh, setThreadRefresh] = useState(0);
  const pendingSession = useRef<ConversationSession | null>(null);
  const navigationTrigger = useRef<HTMLButtonElement>(null);
  const navigation = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!sidebar) return;
    const previous = document.activeElement as HTMLElement | null;
    const first = navigation.current?.querySelector<HTMLElement>("a, button");
    first?.focus();
    const close = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setSidebar(false); navigationTrigger.current?.focus(); }
      if (event.key !== "Tab" || window.matchMedia("(min-width: 1024px)").matches) return;
      const controls = navigation.current?.querySelectorAll<HTMLElement>('a[href], button:not(:disabled), input:not(:disabled), summary');
      if (!controls?.length) return;
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    const resize = () => { if (window.matchMedia("(min-width: 1024px)").matches) setSidebar(false); };
    window.addEventListener("resize", resize);
    window.addEventListener("keydown", close);
    return () => { window.removeEventListener("resize", resize); window.removeEventListener("keydown", close); if (previous?.isConnected) previous.focus(); };
  }, [sidebar]);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const requestedId = new URLSearchParams(window.location.search).get("session");
        const [configuration, active, archived, requested] = await Promise.all([
          api<WorkspaceConfiguration>("/configuration", { signal: controller.signal }),
          api<ConversationSession[]>("/sessions?archived=false&limit=50", { signal: controller.signal }),
          api<ConversationSession[]>("/sessions?archived=true&limit=50", { signal: controller.signal }),
          requestedId && /^[0-9a-f-]{36}$/i.test(requestedId) ? api<ConversationSession>(`/sessions/${requestedId}`, { signal: controller.signal }) : Promise.resolve(null),
        ]);
        if (!controller.signal.aborted) { setConfig(configuration); setSessions([...active, ...archived]); if (requested) setSelected(requested); setError(""); setLoading(false); }
      } catch (error) { if (!controller.signal.aborted) { setError(message(error)); setLoading(false); } }
    })();
    return () => controller.abort();
  }, [refresh]);
  function urlSession(id: string | null) {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("session", id); else url.searchParams.delete("session");
    url.searchParams.delete("auth_error");
    window.history.replaceState({}, "", url);
  }
  function newChat() { if (busy) return; setSelected(null); setDraftRepository(null); setDraftSequence((value) => value + 1); pendingSession.current = null; setSidebar(false); setError(""); urlSession(null); }
  function select(session: ConversationSession) { if (busy) return; setSelected(session); setSidebar(false); pendingSession.current = null; setError(""); urlSession(session.id); }
  const updateSession = useCallback((session: ConversationSession) => {
    setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)].sort((a, b) => b.updated_at.localeCompare(a.updated_at)));
    setSelected((current) => current?.id === session.id ? session : current);
  }, []);
  async function changeRepository(repository: RepositorySelection | null) {
    if (!selected) { setDraftRepository(repository); return; }
    setBusy(true);
    try {
      const updated = await api<ConversationSession>(`/sessions/${selected.id}`, { method: "PATCH", body: JSON.stringify({ repository }) });
      updateSession(updated);
    } finally { setBusy(false); }
  }
  async function send(input: TurnInput) {
    setBusy(true);
    try {
      let current = selected ?? pendingSession.current;
      if (!current) {
        current = await api<ConversationSession>("/sessions", { method: "POST", body: JSON.stringify({ title: input.goal.slice(0, 80), repository: input.repository }) });
        pendingSession.current = current;
      }
      const result = await api<TurnResult>(`/sessions/${current.id}/turns`, { method: "POST", body: JSON.stringify(input) });
      const updated = await api<ConversationSession>(`/sessions/${result.session_id}`);
      pendingSession.current = null; setSelected(updated); updateSession(updated); setThreadRefresh((value) => value + 1); urlSession(updated.id);
    } finally { setBusy(false); }
  }
  const composer = <ConversationComposer menuSide={selected ? "top" : "bottom"} key={selected?.id ?? `new-${draftSequence}`} previousTaskId={selected?.continuation_task_id ?? null} repository={selected ? selected.repository : draftRepository} onRepositoryChange={changeRepository} config={config} disabled={loading || busy || !!selected?.active_task_id} archived={selected?.archived ?? false} onSend={send} />;
  return <div className="flex h-svh min-h-0 overflow-hidden bg-background selection:bg-foreground/15">
    {sidebar ? <button className="fixed inset-0 z-30 bg-foreground/20 lg:hidden" aria-label="Close navigation" onClick={() => setSidebar(false)} /> : null}
    <aside ref={navigation} id="workspace-navigation" className={`fixed inset-y-0 left-0 z-40 flex w-60 shrink-0 flex-col border-r bg-sidebar px-3 pb-3 pt-3 transition-transform motion-reduce:transition-none lg:static lg:translate-x-0 ${sidebar ? "translate-x-0" : "-translate-x-full max-lg:invisible"} ${collapsed ? "lg:hidden" : ""}`} aria-label="Workspace navigation" role={sidebar ? "dialog" : undefined} aria-modal={sidebar || undefined}>
      <div className="mb-5 flex min-h-9 items-center justify-between px-1"><Link href="/workspace" className="inline-flex items-center gap-2 rounded text-[13px] font-semibold focus-visible:outline-2"><span className="flex size-6 items-center justify-center rounded-md bg-primary text-[11px] text-primary-foreground" aria-hidden="true">A</span>AgentForge</Link><Button variant="ghost" size="icon-sm" className="size-11 sm:size-8" aria-label="Collapse sidebar" onClick={() => { setSidebar(false); setCollapsed(true); }}><PanelLeftClose className="size-3.5" /></Button></div>
      <Button variant="ghost" className="mb-3 min-h-11 justify-start gap-2 px-2 text-[13px] sm:min-h-9" onClick={newChat} disabled={busy}><SquarePen className="size-4" />New conversation</Button>
      <SessionHistory sessions={sessions} selectedId={selected?.id ?? null} loading={loading} disabled={busy} onSelect={select} onChange={updateSession} />
      <div className="space-y-3 border-t px-1 pt-2"><Link href="/settings" className="flex min-h-11 items-center gap-2 rounded-md px-2 text-xs text-muted-foreground hover:bg-sidebar-accent hover:text-foreground focus-visible:outline-2 sm:min-h-8"><Settings className="size-3.5" aria-hidden="true" />Settings</Link><AccountControl /></div>
    </aside>
    <div inert={sidebar} className="flex min-w-0 flex-1 flex-col">
      <header className="flex min-h-14 shrink-0 items-center gap-2 px-4 sm:px-6"><Button ref={navigationTrigger} variant="ghost" size="icon" aria-label="Open navigation" aria-expanded={sidebar} aria-controls="workspace-navigation" className={`size-11 sm:size-8 ${collapsed ? "" : "lg:hidden"}`} onClick={() => { setCollapsed(false); setSidebar(!window.matchMedia("(min-width: 1024px)").matches); }}><Menu className="size-4" /></Button><p className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{selected?.title ?? "New conversation"}</p>{selected?.archived ? <span className="text-xs text-muted-foreground">Archived</span> : null}</header>
      {error ? <div role="alert" className="mx-5 mb-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2 text-xs text-destructive"><p>{error}</p><Button variant="outline" size="sm" onClick={() => setRefresh((value) => value + 1)}>Retry</Button></div> : null}
      <main data-conversation-scroll className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto overscroll-contain">
        {selected ? <ConversationThread key={selected.id} session={selected} refresh={threadRefresh} onChange={updateSession} /> : <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-5 pb-10 sm:px-8"><h1 className="mb-6 text-center text-2xl font-medium tracking-tight">What are we working on?</h1>{composer}</div>}
        {selected ? <div className="sticky bottom-0 mt-auto bg-background px-5 pb-4 pt-3 sm:px-8"><div className="mx-auto max-w-[44rem]">{composer}</div></div> : null}
      </main>
    </div>
  </div>;
}
