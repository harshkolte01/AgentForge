"use client";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Menu, PanelLeftClose, Plug, SquarePen } from "lucide-react";
import { ScrollToBottom } from "@/features/sessions/scroll-to-bottom";
import { SidebarAccountMenu } from "@/features/sessions/sidebar-account-menu";
import { ResizableSidebar } from "@/features/sessions/resizable-sidebar";
import { Button, buttonVariants } from "@/components/ui/button";
import { SessionHistory } from "@/features/sessions/session-history";
import { ConversationComposer } from "@/features/sessions/conversation-composer";
import { ConversationThread, DraftThread } from "@/features/sessions/conversation-thread";
import { ConversationScrollContext, useConversationScroll } from "@/features/sessions/use-conversation-scroll";
import { ConversationQueue } from "@/features/sessions/conversation-queue";
import { DeliveryChip } from "@/features/delivery/delivery-chip";
import { DeliveryProvider } from "@/features/delivery/delivery-provider";
import { DeliverySplit } from "@/features/delivery/delivery-split";
import { BrandMark } from "@/components/brand-mark";
import { FirstRun } from "@/features/sessions/first-run";
import { mergeSessions, upsertSession } from "@/lib/conversation-order";
import { hasUnseenOutcome } from "@/lib/conversation-status";
import { usePageVisible } from "@/lib/use-page-visible";
import { accountId, deliverIntent, pendingTurns, saveIntent } from "@/lib/conversation-outbox";
import { navigationAfterSend, receiptEffects, sessionAfterReceipt, type PendingTurn, type Receipt } from "@/features/sessions/turn-flow";
import { api, ApiError, message, terminalStatuses } from "@/lib/task-api";
import { rememberChat, routes, uuidPattern } from "@/lib/routes";
import { useDocumentTitle } from "@/lib/use-document-title";
import type { ConversationSession, RepositorySelection, TurnInput, WorkspaceConfiguration } from "@/lib/workspace-api";

const APP_NAME = "AgentForge";

/**
 * The URL is the source of truth: `/chat` is a new conversation, `/chat/<id>` an existing one, and `/plugins` is a page of its
 * own (`children`) shown in place of the thread, with the same sidebar.
 */
export function ConversationWorkspace({ children }: { children?: ReactNode }) {
  const router = useRouter();
  const { controller: scrollController, attach: scrollerRef } = useConversationScroll();
  const page = usePathname().startsWith(routes.plugins());
  const params = useParams<{ id?: string }>();
  const routeId = typeof params.id === "string" ? params.id.toLowerCase() : null;
  const [sessions, setSessions] = useState<ConversationSession[]>([]);
  const [lookupFailure, setLookupFailure] = useState<{ id: string; missing: boolean } | null>(null);
  const [draftSequence, setDraftSequence] = useState(0);
  const [draftRepository, setDraftRepository] = useState<RepositorySelection | null>(null);
  // The plugins chosen for a conversation not created yet; null until the person chooses (it then starts with the ones left on in Settings).
  const [config, setConfig] = useState<WorkspaceConfiguration | null>(null);
  const [sidebar, setSidebar] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // The message just sent (shown at once) and, once the server accepted it, its receipt: the thread reconciles straight from both.
  const [pending, setPending] = useState<PendingTurn | null>(null);
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  // A follow-up read of the conversation failed after the message was accepted: shown as a quiet retry, never blocking.
  const [staleSession, setStaleSession] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [threadRefresh, setThreadRefresh] = useState(0);
  const pendingSession = useRef<ConversationSession | null>(null);
  // The composer that sent a conversation's first message stays the same element when that conversation opens (no remount, no
  // lost focus): its key is kept for the conversation created from it.
  const [adopted, setAdopted] = useState<{ key: string; composerKey: string; sessionId?: string } | null>(null);
  const navigationTrigger = useRef<HTMLButtonElement>(null);
  const navigation = useRef<HTMLElement>(null);
  const [retryPending, setRetryPending] = useState(0);
  const routeRef = useRef(routeId);
  // The chat "Back to chat" from Settings returns to is the last conversation shown, not the Plugins page.
  useEffect(() => { routeRef.current = routeId; if (!page) rememberChat(routeId ? routes.conversation(routeId) : routes.newConversation); }, [routeId, page]);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      const account = await accountId();
      const waiting = await pendingTurns(account);
      for (const intent of waiting) {
        if (controller.signal.aborted) return;
        const { result } = await deliverIntent(intent, controller.signal);
        if (controller.signal.aborted) return;
        const updated = await api<ConversationSession>(`/sessions/${result.session_id}`, { signal: controller.signal });
        setSessions((current) => [updated, ...current.filter((item) => item.id !== updated.id)]);
        setThreadRefresh((value) => value + 1);
        if (!routeRef.current) router.replace(routes.conversation(updated.id));
      }
    })().catch((failed) => { if (!controller.signal.aborted) setError(message(failed)); });
    return () => controller.abort();
  }, [router, retryPending]);
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
        if (!controller.signal.aborted) { setConfig(configuration); setSessions((current) => mergeSessions(current, active, archived)); setError(""); setLoading(false); }
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
  useDocumentTitle(`${page ? "Plugins & skills" : missing ? "Conversation not found" : selected?.title ?? "New conversation"} · ${APP_NAME}`);
  /** Resets draft state for a fresh conversation; navigation is done by the caller's link or router call. */
  function newChat() { if (busy) return false; setDraftRepository(null); setDraftSequence((value) => value + 1); pendingSession.current = null; setSidebar(false); setError(""); return true; }
  function select() { setSidebar(false); pendingSession.current = null; setError(""); }
  // Stable (it only uses state setters): the thread's store keeps the first one it is given.
  // A read that changes nothing (every poll of an idle conversation) keeps the same list, so nothing re-renders.
  const updateSession = useCallback((session: ConversationSession) => {
    setSessions((current) => upsertSession(current, session));
    // Any successful read of a conversation ends its "could not refresh" notice.
    setStaleSession((current) => (current === session.id ? null : current));
  }, []);
  // Looking at a conversation is what reads it: once its latest outcome (a reply, or a failure) is open in a tab you are
  // looking at, the server is told, which clears "new reply" / "failed" beside it in the sidebar. A failed call is tried
  // again with the thread's next refresh; a conversation is told about at most once per message it has.
  const pageVisible = usePageVisible();
  const seenThrough = useRef(new Map<string, number>());
  useEffect(() => {
    if (!selected || !hasUnseenOutcome(selected) || !pageVisible) return;
    const { id, last_sequence: through } = selected;
    if (seenThrough.current.get(id) === through) return;
    seenThrough.current.set(id, through);
    void api<ConversationSession>(`/sessions/${id}/read`, { method: "POST", body: JSON.stringify({ sequence: through }) })
      .then(updateSession)
      .catch(() => { if (seenThrough.current.get(id) === through) seenThrough.current.delete(id); });
  }, [selected, pageVisible, updateSession]);
  async function changeRepository(repository: RepositorySelection | null) {
    if (!selected) { setDraftRepository(repository); return; }
    setBusy(true);
    try {
      const updated = await api<ConversationSession>(`/sessions/${selected.id}`, { method: "PATCH", body: JSON.stringify({ repository }) });
      updateSession(updated);
    } finally { setBusy(false); }
  }
  async function stop() {
    if (selected?.active_task_id) updateSession(await api<ConversationSession>(`/sessions/${selected.id}/queue/stop`, {
      method: "POST", body: JSON.stringify({ task_id: selected.active_task_id }) }));
  }
  /** Reads the conversation after a send; a failure is a quiet notice with a retry, not an error screen, because the message is already safe. */
  const refreshAfterSend = useCallback((sessionId: string) => {
    void api<ConversationSession>(`/sessions/${sessionId}`).then(updateSession).catch(() => {
      // Only the conversation the reader is looking at is told about it.
      if (routeRef.current === sessionId) setStaleSession(sessionId);
    });
  }, [updateSession]);
  const receiptTaken = useCallback((taken: Receipt) => {
    setReceipt((current) => (current === taken ? null : current));
    setPending((current) => (current?.key === taken.key ? null : current));
  }, []);
  async function send(input: TurnInput) {
    const key = input.idempotency_key;
    const routeAtSend = routeId;
    setBusy(true);
    // The bubble is keyed by this turn's idempotency key for life, so the server's copy of it replaces it in place. A first
    // message shows at once too, in a draft of the thread, while its conversation is being created.
    setPending({ key, sessionId: selected?.id ?? null, goal: input.goal, attachments: input.attachments.map(({ name }) => ({ name })) });
    if (!selected) setAdopted({ key, composerKey: `new-${draftSequence}` });
    try {
      const account = await accountId();
      const intent = await saveIntent(account, selected?.id ?? pendingSession.current?.id ?? null, input);
      const { result, session: created } = await deliverIntent(intent);
      if (created) updateSession(created);
      // The draft now belongs to its conversation: the thread that opens for it picks up the same bubble.
      setPending((current) => (current?.key === key && current.sessionId === null ? { ...current, sessionId: result.session_id } : current));
      setAdopted((current) => (current?.key === key ? { ...current, sessionId: result.session_id } : current));
      // Reconcile from the accepted receipt itself: the message and the run to follow are known even if reading the conversation fails.
      const effects = receiptEffects(result, (status) => terminalStatuses.has(status));
      setSessions((current) => current.map((item) => (item.id === result.session_id ? sessionAfterReceipt(item, effects) : item)));
      // The pending bubble stays until the thread has applied this receipt (`receiptTaken`), so there is no frame without your message.
      setReceipt({ key, result });
      pendingSession.current = null; setThreadRefresh((value) => value + 1);
      if (navigationAfterSend({ routeAtSend, routeNow: routeRef.current, resultSessionId: result.session_id }) === "navigate") router.push(routes.conversation(result.session_id));
      refreshAfterSend(result.session_id);
    } catch (error) {
      setPending((current) => (current?.key === key ? null : current));
      setAdopted((current) => (current?.key === key ? null : current));
      throw error;
    }
    finally { setBusy(false); }
  }
  // "Try again" on a failed reply sends the same message once more, through the normal send path (a fresh turn, so it is not a replay).
  const retry = (goal: string) => {
    void send({ routing: "auto", idempotency_key: crypto.randomUUID(), goal, repository: selected ? selected.repository : null, allowed_web_hosts: [], attachments: [] }).catch(() => {});
  };
  // The delivery panel's "Fix checks" and friends: a canned message through the same send path as anything you type.
  const askAgent = (goal: string) => send({ routing: "auto", idempotency_key: crypto.randomUUID(), goal, repository: selected ? selected.repository : null, allowed_web_hosts: [], attachments: [] });
  // A first message being sent: shown at once in a draft of its thread, until the conversation it creates opens.
  const draft = !selected && pending && (routeId === null || pending.sessionId === routeId) ? pending : null;
  // The composer sits under the thread once there is one (or a draft of it); before that it is centred in the empty chat.
  const docked = !!selected || !!draft;
  const composerKey = selected ? (adopted?.sessionId === selected.id ? adopted.composerKey : selected.id) : `new-${draftSequence}`;
  const composer = <ConversationComposer queue={selected && !!selected.queued_count ? <ConversationQueue key={selected.id} session={selected} onChange={updateSession} /> : null} menuSide={docked ? "top" : "bottom"} key={composerKey} sessionId={selected?.id ?? null} contextRevision={selected ? `${selected.revision ?? selected.last_sequence}:${selected.active_task_id ?? ""}` : "new"} onNewConversation={() => { if (newChat()) router.push(routes.newConversation); }} repository={selected ? selected.repository : draftRepository} repositoryLocked={!!selected?.queued_count} onRepositoryChange={changeRepository} config={config} disabled={loading || busy} running={!!selected?.active_task_id} runId={selected?.active_task_id ?? null} onStop={stop} archived={selected?.archived ?? false} onSend={send} docked={docked} below={docked ? undefined : <FirstRun config={config} />} />;
  return <ConversationScrollContext value={scrollController}><DeliveryProvider sessionId={selected?.id ?? null} hasRepository={!!selected?.repository} activeTaskId={selected?.active_task_id ?? null} onAskAgent={askAgent}><div data-conversation-root className="relative top-[var(--visible-top,0px)] flex h-[var(--visible-height,100dvh)] min-h-0 overflow-hidden bg-background selection:bg-foreground/15">
    {sidebar ? <button className="fixed inset-0 z-30 bg-foreground/20 lg:hidden" aria-label="Close navigation" onClick={() => setSidebar(false)} /> : null}
    <ResizableSidebar collapsed={collapsed} ref={navigation} id="workspace-navigation" className={`fixed inset-y-0 left-0 z-40 flex shrink-0 flex-col border-r bg-sidebar px-3 pb-3 pt-3 transition-transform motion-reduce:transition-none lg:translate-x-0 ${sidebar ? "translate-x-0" : "-translate-x-full max-lg:invisible"} ${collapsed ? "lg:hidden" : ""}`} aria-label="Workspace navigation" role={sidebar ? "dialog" : undefined} aria-modal={sidebar || undefined}>
      <div className="mb-5 flex min-h-9 items-center justify-between px-1"><Link href={routes.newConversation} onClick={(event) => { if (!newChat()) event.preventDefault(); }} className="inline-flex items-center gap-2 rounded text-[13px] font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring/60"><BrandMark />AgentForge</Link><Button variant="ghost" size="icon-sm" className="size-11 sm:size-8" aria-label="Collapse sidebar" onClick={() => { setSidebar(false); setCollapsed(true); }}><PanelLeftClose className="size-3.5" /></Button></div>
      <Button variant="ghost" className="mb-3 min-h-11 justify-start gap-2 px-2 text-[13px] sm:min-h-9" onClick={() => { if (newChat()) router.push(routes.newConversation); }} disabled={busy}><SquarePen className="size-4" />New conversation</Button>
      <Link href={routes.plugins()} aria-current={page ? "page" : undefined} onClick={() => setSidebar(false)} className={buttonVariants({ variant: "ghost", className: `mb-3 min-h-11 w-full justify-start gap-2 px-2 text-[13px] sm:min-h-9 ${page ? "bg-muted text-foreground" : ""}` })}><Plug className="size-4" aria-hidden="true" />Plugins</Link>
      <SessionHistory sessions={sessions} selectedId={selected?.id ?? null} loading={loading} disabled={busy} onSelect={select} onChange={updateSession} />
      <div className="border-t pt-2"><SidebarAccountMenu /></div>
    </ResizableSidebar>
    <div inert={sidebar} className="flex min-w-0 flex-1"><DeliverySplit>
      <header className={`flex min-h-12 shrink-0 items-center px-3 sm:px-4 ${collapsed ? "" : "lg:hidden"}`}><Button ref={navigationTrigger} variant="ghost" size="icon" aria-label="Open navigation" aria-expanded={sidebar} aria-controls="workspace-navigation" className={`size-11 sm:size-8 ${collapsed ? "" : "lg:hidden"}`} onClick={() => { setCollapsed(false); setSidebar(!window.matchMedia("(min-width: 1024px)").matches); }}><Menu className="size-4" /></Button>
      </header>
      {/* No title bar: the sidebar and the tab already name the conversation. What is left floats: its delivery status, and a quiet note when archived. */}
      {page ? null : <h1 className="sr-only">{missing ? "Conversation not found" : failure ? "Unavailable" : selected?.title ?? (resolving ? "Loading…" : "New conversation")}</h1>}
      <div className="absolute right-3 top-3 z-20 flex items-center gap-2 sm:right-4">
        {selected?.archived ? <span className="rounded-full bg-muted px-2.5 py-1 text-xs text-muted-foreground">Archived</span> : null}{page ? null : <DeliveryChip />}
      </div>
      {error ? <div role="alert" className="mx-5 mb-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border px-3 py-2 text-xs text-destructive"><p>{error}</p><Button variant="outline" size="sm" onClick={() => { setLookupFailure(null); setRefresh((value) => value + 1); setRetryPending((value) => value + 1); }}>Retry</Button></div> : null}
      {/* The scrollbar is hidden (wheel, touch, keys and the jump-to-latest button still scroll); the top padding keeps the floating status clear of the thread. */}
      <main ref={page ? undefined : scrollerRef} data-conversation-scroll={page ? undefined : ""} className={`relative flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto overscroll-contain [overflow-anchor:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden ${collapsed || page ? "" : "lg:pt-12"}`}>
        {page ? children : <>
        {failure && !failure.missing ? <div role="alert" className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center gap-3 px-5 pb-10 text-center sm:px-8"><h2 className="text-2xl font-medium tracking-tight">Couldn&apos;t load this conversation</h2><p className="text-sm leading-6 text-muted-foreground">Check your connection and try again.</p><div><Button onClick={() => { setLookupFailure(null); setRefresh((value) => value + 1); }}>Try again</Button></div></div>
        : missing ? <div role="alert" className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center gap-3 px-5 pb-10 text-center sm:px-8"><h2 className="text-2xl font-medium tracking-tight">Conversation not found</h2><p className="text-sm leading-6 text-muted-foreground">It may have been deleted, or the link belongs to a different account.</p><div><Link href={routes.newConversation} onClick={(event) => { if (!newChat()) event.preventDefault(); }} className="inline-flex min-h-11 items-center rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground outline-none hover:bg-primary/85 focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background sm:min-h-9">Start a new conversation</Link></div></div>
        : draft ? <DraftThread pending={draft} />
        : resolving ? <div role="status" className="mx-auto flex w-full max-w-3xl animate-pulse flex-col gap-6 px-5 pb-6 pt-4 motion-reduce:animate-none sm:px-8"><span className="sr-only">Loading conversation…</span><div className="ml-auto h-10 w-2/5 rounded-3xl bg-muted" /><div className="space-y-2.5"><div className="h-3.5 w-11/12 rounded bg-muted" /><div className="h-3.5 w-3/4 rounded bg-muted" /></div></div>
        : selected ? <ConversationThread key={selected.id} session={selected} refresh={threadRefresh} pending={pending} receipt={receipt} onReceipt={receiptTaken} onChange={updateSession} onRetry={retry} /> : null}
        {/* One element whether centred in the empty chat or docked under the thread, so sending a first message never remounts the
            composer (its text, focus and in-flight send stay with it). */}
        {docked || (!failure && !missing && !resolving) ? <div data-conversation-composer={docked ? "" : undefined} className={docked ? "sticky bottom-0 mt-auto bg-background px-5 pb-4 pt-1 before:pointer-events-none before:absolute before:inset-x-0 before:-top-6 before:h-6 before:bg-gradient-to-t before:from-background before:to-transparent sm:px-8" : "mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-5 pb-10 sm:px-8"}>
          {docked ? null : <h2 className="mb-6 text-center text-2xl font-medium tracking-tight">What are we working on?</h2>}
          <div className={docked ? "relative mx-auto max-w-[44rem]" : undefined}>
            {docked ? <ScrollToBottom /> : null}
            {selected && staleSession === selected.id ? <div role="status" data-refresh-notice className="mb-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-background px-3 py-1.5 text-xs text-muted-foreground"><span>Message sent. The conversation could not be refreshed.</span><Button variant="ghost" size="sm" className="pointer-coarse:min-h-11" onClick={() => { setStaleSession(null); refreshAfterSend(selected.id); setThreadRefresh((value) => value + 1); }}>Retry</Button></div> : null}
            {composer}
          </div>
        </div> : null}
        </>}
      </main>
    </DeliverySplit></div>
  </div></DeliveryProvider></ConversationScrollContext>;
}
