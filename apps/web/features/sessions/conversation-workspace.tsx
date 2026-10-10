"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { ArrowLeft, Menu, PanelLeftClose, SquarePen } from "lucide-react";
import { ScrollToBottom } from "@/features/sessions/scroll-to-bottom";
import { SidebarAccountMenu } from "@/features/sessions/sidebar-account-menu";
import { ResizableSidebar } from "@/features/sessions/resizable-sidebar";
import { Button } from "@/components/ui/button";
import { SessionHistory } from "@/features/sessions/session-history";
import { ConversationComposer } from "@/features/sessions/conversation-composer";
import { ConversationThread } from "@/features/sessions/conversation-thread";
import { api, ApiError, message } from "@/lib/task-api";
import { routes, uuidPattern } from "@/lib/routes";
import { useDocumentTitle } from "@/lib/use-document-title";
import type { ConversationSession, RepositorySelection, TurnInput, TurnResult, WorkspaceConfiguration } from "@/lib/workspace-api";

const APP_NAME = "AgentForge";

/** The URL is the source of truth: `/chat` is a new conversation, `/chat/<id>` an existing one, `/chat/<id>/runs/<taskId>` its run details. */
export function ConversationWorkspace({ children }: { children: ReactNode }) {
  const router = useRouter();
  const params = useParams<{ id?: string; taskId?: string }>();
  const routeId = typeof params.id === "string" ? params.id.toLowerCase() : null;
  const runId = typeof params.taskId === "string" ? params.taskId : null;
  const [sessions, setSessions] = useState<ConversationSession[]>([]);
  const [lookupFailure, setLookupFailure] = useState<{ id: string; missing: boolean } | null>(null);
  const [draftSequence, setDraftSequence] = useState(0);
  const [draftRepository, setDraftRepository] = useState<RepositorySelection | null>(null);
  const [config, setConfig] = useState<WorkspaceConfiguration | null>(null);
  const [sidebar, setSidebar] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<string | null>(null);
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
      const controls = Array.from(navigation.current?.querySelectorAll<HTMLElement>('a[href], button:not(:disabled), input:not(:disabled), summary') ?? []).filter((control) => control.tabIndex >= 0 && control.getClientRects().length > 0);
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
        const [configuration, active, archived] = await Promise.all([
          api<WorkspaceConfiguration>("/configuration", { signal: controller.signal }),
          api<ConversationSession[]>("/sessions?archived=false&limit=50", { signal: controller.signal }),
          api<ConversationSession[]>("/sessions?archived=true&limit=50", { signal: controller.signal }),
        ]);
        if (!controller.signal.aborted) { setConfig(configuration); setSessions((current) => [...new Map([...active, ...archived, ...current].map((item) => [item.id, item])).values()]); setError(""); setLoading(false); }
      } catch (error) { if (!controller.signal.aborted) { setError(message(error)); setLoading(false); } }
    })();
    return () => controller.abort();
  }, [refresh]);
  const validRoute = routeId === null || uuidPattern.test(routeId);
  const selected = routeId && validRoute ? sessions.find((item) => item.id === routeId) ?? null : null;
  const known = !!selected;
  const failure = routeId && lookupFailure?.id === routeId ? lookupFailure : null;
  const missing = !validRoute || !!failure?.missing;
  const resolving = !!routeId && validRoute && !known && !failure;
  // A conversation that is not in the loaded history page (deep link, reload, older chat) is fetched by id.
  useEffect(() => {
    if (!routeId || !uuidPattern.test(routeId) || known) return;
    const controller = new AbortController();
    void api<ConversationSession>(`/sessions/${routeId}`, { signal: controller.signal }).then((session) => {
      if (controller.signal.aborted) return;
      pendingSession.current = null;
      setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)]);
    }).catch((failed) => {
      if (!controller.signal.aborted) setLookupFailure({ id: routeId, missing: failed instanceof ApiError && failed.status === 404 });
    });
    return () => controller.abort();
  }, [routeId, known, refresh]);
  useDocumentTitle(`${runId ? "Run details" : missing ? "Conversation not found" : selected?.title ?? "New conversation"} · ${APP_NAME}`);
  /** Resets draft state for a fresh conversation; navigation is done by the caller's link or router call. */
  function newChat() { if (busy) return false; setDraftRepository(null); setDraftSequence((value) => value + 1); pendingSession.current = null; setSidebar(false); setError(""); return true; }
  function select() { setSidebar(false); pendingSession.current = null; setError(""); }
  function updateSession(session: ConversationSession) {
    setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)].sort((a, b) => b.updated_at.localeCompare(a.updated_at)));
  }
  async function changeRepository(repository: RepositorySelection | null) {
    if (!selected) { setDraftRepository(repository); return; }
    setBusy(true);
    try {
      const updated = await api<ConversationSession>(`/sessions/${selected.id}`, { method: "PATCH", body: JSON.stringify({ repository }) });
      updateSession(updated);
    } finally { setBusy(false); }
  }
  async function stop() {
    if (selected?.active_task_id) await api(`/tasks/${selected.active_task_id}/cancel`, { method: "POST" });
  }
  const settlePending = () => setPending(null);
  async function send(input: TurnInput) {
    setBusy(true);
    if (selected) setPending(input.goal);
    try {
      let current = selected ?? pendingSession.current;
      if (!current) {
        current = await api<ConversationSession>("/sessions", { method: "POST", body: JSON.stringify({ title: input.goal.slice(0, 80), repository: input.repository }) });
        pendingSession.current = current;
      }
      const result = await api<TurnResult>(`/sessions/${current.id}/turns`, { method: "POST", body: JSON.stringify(input) });
      const updated = await api<ConversationSession>(`/sessions/${result.session_id}`);
      pendingSession.current = null; updateSession(updated); setThreadRefresh((value) => value + 1); if (routeId !== updated.id) router.push(routes.conversation(updated.id));
    } catch (error) { setPending(null); throw error; }
    finally { setBusy(false); }
  }
  const composer = <ConversationComposer menuSide={selected ? "top" : "bottom"} key={selected?.id ?? `new-${draftSequence}`} sessionId={selected?.id ?? null} contextRevision={selected ? `${selected.last_sequence}:${selected.active_task_id ?? ""}` : "new"} onNewConversation={() => { if (newChat()) router.push(routes.newConversation); }} previousTaskId={selected?.continuation_task_id ?? null} repository={selected ? selected.repository : draftRepository} onRepositoryChange={changeRepository} config={config} disabled={loading || busy} running={!!selected?.active_task_id} onStop={stop} archived={selected?.archived ?? false} onSend={send} />;
  return <div className="flex h-svh min-h-0 overflow-hidden bg-background selection:bg-foreground/15">
    {sidebar ? <button className="fixed inset-0 z-30 bg-foreground/20 lg:hidden" aria-label="Close navigation" onClick={() => setSidebar(false)} /> : null}
    <ResizableSidebar collapsed={collapsed} ref={navigation} id="workspace-navigation" className={`fixed inset-y-0 left-0 z-40 flex shrink-0 flex-col border-r bg-sidebar px-3 pb-3 pt-3 transition-transform motion-reduce:transition-none lg:translate-x-0 ${sidebar ? "translate-x-0" : "-translate-x-full max-lg:invisible"} ${collapsed ? "lg:hidden" : ""}`} aria-label="Workspace navigation" role={sidebar ? "dialog" : undefined} aria-modal={sidebar || undefined}>
      <div className="mb-5 flex min-h-9 items-center justify-between px-1"><Link href={routes.newConversation} onClick={(event) => { if (!newChat()) event.preventDefault(); }} className="inline-flex items-center gap-2 rounded text-[13px] font-semibold focus-visible:outline-2"><span className="flex size-6 items-center justify-center rounded-md bg-primary text-[11px] text-primary-foreground" aria-hidden="true">A</span>AgentForge</Link><Button variant="ghost" size="icon-sm" className="size-11 sm:size-8" aria-label="Collapse sidebar" onClick={() => { setSidebar(false); setCollapsed(true); }}><PanelLeftClose className="size-3.5" /></Button></div>
      <Button variant="ghost" className="mb-3 min-h-11 justify-start gap-2 px-2 text-[13px] sm:min-h-9" onClick={() => { if (newChat()) router.push(routes.newConversation); }} disabled={busy}><SquarePen className="size-4" />New conversation</Button>
      <SessionHistory sessions={sessions} selectedId={selected?.id ?? null} loading={loading} disabled={busy} onSelect={select} onChange={updateSession} />
      <div className="border-t pt-2"><SidebarAccountMenu /></div>
    </ResizableSidebar>
    <div inert={sidebar} className="flex min-w-0 flex-1 flex-col">
      <header className="flex min-h-14 shrink-0 items-center gap-2 px-4 sm:px-6"><Button ref={navigationTrigger} variant="ghost" size="icon" aria-label="Open navigation" aria-expanded={sidebar} aria-controls="workspace-navigation" className={`size-11 sm:size-8 ${collapsed ? "" : "lg:hidden"}`} onClick={() => { setCollapsed(false); setSidebar(!window.matchMedia("(min-width: 1024px)").matches); }}><Menu className="size-4" /></Button>
        {runId && routeId ? <nav aria-label="Breadcrumb" className="flex min-w-0 flex-1 items-center gap-2 text-xs text-muted-foreground"><Link href={routes.conversation(routeId)} className="inline-flex min-h-11 shrink-0 items-center gap-1.5 rounded text-foreground hover:underline focus-visible:outline-2 sm:min-h-8"><ArrowLeft className="size-3.5" aria-hidden="true" />Back to conversation</Link><span aria-hidden="true">/</span><span className="truncate" aria-current="page">{selected?.title ? `${selected.title} · Run details` : "Run details"}</span></nav>
          : <p className="min-w-0 flex-1 truncate text-xs text-muted-foreground">{missing ? "Conversation not found" : failure ? "Unavailable" : selected?.title ?? (resolving ? "Loading…" : "New conversation")}</p>}
        {selected?.archived ? <span className="text-xs text-muted-foreground">Archived</span> : null}</header>
      {error ? <div role="alert" className="mx-5 mb-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2 text-xs text-destructive"><p>{error}</p><Button variant="outline" size="sm" onClick={() => { setLookupFailure(null); setRefresh((value) => value + 1); }}>Retry</Button></div> : null}
      {runId ? <main className="min-h-0 min-w-0 flex-1 overflow-y-auto overscroll-contain"><div className="mx-auto w-full max-w-4xl px-5 pb-16 pt-2 sm:px-8">{children}</div></main> : null}
      <main hidden={!!runId} data-conversation-scroll className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto overscroll-contain">
        {failure && !failure.missing ? <div role="alert" className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center gap-3 px-5 pb-10 text-center sm:px-8"><h1 className="text-2xl font-medium tracking-tight">Couldn&apos;t load this conversation</h1><p className="text-sm leading-6 text-muted-foreground">Check your connection and try again.</p><div><Button onClick={() => { setLookupFailure(null); setRefresh((value) => value + 1); }}>Try again</Button></div></div>
        : missing ? <div role="alert" className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center gap-3 px-5 pb-10 text-center sm:px-8"><h1 className="text-2xl font-medium tracking-tight">Conversation not found</h1><p className="text-sm leading-6 text-muted-foreground">It may have been deleted, or the link belongs to a different account.</p><div><Link href={routes.newConversation} onClick={(event) => { if (!newChat()) event.preventDefault(); }} className="inline-flex min-h-11 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/85 focus-visible:outline-2 focus-visible:outline-offset-2 sm:min-h-9">Start a new conversation</Link></div></div>
        : resolving ? <div role="status" className="mx-auto flex w-full max-w-3xl animate-pulse flex-col gap-6 px-5 pb-6 pt-4 motion-reduce:animate-none sm:px-8"><span className="sr-only">Loading conversation…</span><div className="ml-auto h-10 w-2/5 rounded-3xl bg-muted" /><div className="space-y-2.5"><div className="h-3.5 w-11/12 rounded bg-muted" /><div className="h-3.5 w-3/4 rounded bg-muted" /></div></div>
        : selected ? <ConversationThread key={selected.id} session={selected} refresh={threadRefresh} pending={pending} onSettled={settlePending} onChange={updateSession} /> : <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-5 pb-10 sm:px-8"><h1 className="mb-6 text-center text-2xl font-medium tracking-tight">What are we working on?</h1>{composer}</div>}
        {selected ? <div className="sticky bottom-0 mt-auto bg-background px-5 pb-4 pt-1 before:pointer-events-none before:absolute before:inset-x-0 before:-top-6 before:h-6 before:bg-gradient-to-t before:from-background before:to-transparent sm:px-8"><div className="relative mx-auto max-w-[44rem]"><ScrollToBottom />{composer}</div></div> : null}
      </main>
    </div>
  </div>;
}
