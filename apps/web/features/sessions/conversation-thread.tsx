"use client";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { AssistantMessage } from "@/features/sessions/assistant-message";
import { CopyButton } from "@/features/sessions/copy-button";
import { ActivityDisclosure } from "@/features/sessions/run-steps";
import { TaskTurn, ThinkingIndicator } from "@/features/sessions/task-turn";
import { Button } from "@/components/ui/button";
import { api, message, formatDate } from "@/lib/task-api";
import { routes } from "@/lib/routes";
import { contentBelow, scrollToContentEnd, scrollTurnToTop } from "@/lib/scroll";
import { actionCount } from "@/lib/tool-steps";
import type { ConversationMessage, ConversationSession } from "@/lib/workspace-api";

const nearBottomPx = 96;
/** Space kept above and below the turn so it can be scrolled to the top with a comfortable gap. */
const turnClearance = 40;

/** Terminal receipts written by the server read better as plain sentences. */
function readable(content: string) {
  const status = /^Task ended with status (\w+)\.$/.exec(content)?.[1];
  if (status === "CANCELLED") return "Stopped.";
  if (status === "FAILED") return "Something went wrong and I couldn't finish. Please try again.";
  return content;
}

const actionsClass = "flex min-h-8 items-center gap-0.5 text-xs text-muted-foreground opacity-0 transition-opacity group-hover/message:opacity-100 group-focus-within/message:opacity-100 [@media(hover:none)]:opacity-100 motion-reduce:transition-none";

export function ConversationThread({ session, refresh, pending, onSettled, onChange }: { session: ConversationSession; refresh: number; pending: string | null; onSettled: () => void; onChange: (session: ConversationSession) => void }) {
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [olderBusy, setOlderBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const turn = useRef<HTMLDivElement>(null);
  const [room, setRoom] = useState(0);
  const [anchored, setAnchored] = useState(false);
  const records = useRef<ConversationMessage[]>([]);
  const [fresh, setFresh] = useState<ReadonlySet<string>>(new Set());
  // Runs whose reply already appeared live: their saved message takes over in place, without a second entrance.
  const [streamed, setStreamed] = useState<ReadonlySet<string>>(new Set());
  const stick = useRef(true);
  // A turn in flight is anchored near the top of the view with room beneath it, like a chat that "starts a new page".
  const running = !!session.active_task_id;
  // Once a turn has been anchored its room stays, so the reply finishing never makes the page jump.
  if ((pending || running) && !anchored) setAnchored(true);
  const reserve = anchored || running || !!pending;
  const lastRefresh = useRef(refresh);
  const changeCallback = useRef(onChange);
  const settled = useRef(onSettled);
  useEffect(() => { changeCallback.current = onChange; settled.current = onSettled; }, [onChange, onSettled]);
  const scroller = useCallback(() => root.current?.closest<HTMLElement>("[data-conversation-scroll]") ?? null, []);
  const followBottom = useCallback(() => {
    const element = scroller();
    if (element && stick.current) scrollToContentEnd(element);
  }, [scroller]);
  const anchorTurn = useCallback((behavior: ScrollBehavior = "smooth") => {
    const element = scroller();
    if (!element || !turn.current) return;
    stick.current = false;
    scrollTurnToTop(element, turn.current, 20, behavior);
  }, [scroller]);
  useEffect(() => {
    const element = scroller();
    if (!element) return;
    const track = () => { stick.current = contentBelow(element) < nearBottomPx; };
    element.addEventListener("scroll", track, { passive: true });
    // Room below the turn = what the reader can see, so the turn can sit at the top of the view.
    const measure = () => {
      const composer = root.current?.nextElementSibling as HTMLElement | null;
      setRoom(Math.max(0, element.clientHeight - (composer?.offsetHeight ?? 0) - turnClearance));
    };
    const frame = requestAnimationFrame(measure);
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    if (root.current?.nextElementSibling) observer.observe(root.current.nextElementSibling);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); element.removeEventListener("scroll", track); };
  }, [scroller]);
  useEffect(() => {
    const controller = new AbortController();
    const forSend = refresh !== lastRefresh.current;
    void (async () => {
      try {
        const next = await api<ConversationSession>(`/sessions/${session.id}`, { signal: controller.signal });
        let cursor = records.current.length ? records.current[records.current.length - 1].sequence : Math.max(0, next.last_sequence - 100);
        const additions: ConversationMessage[] = [];
        for (let page = 0; page < 4; page++) {
          const batch = await api<ConversationMessage[]>(`/sessions/${session.id}/messages?after_sequence=${cursor}&limit=100`, { signal: controller.signal });
          additions.push(...batch);
          if (batch.length < 100) break;
          cursor = batch[batch.length - 1].sequence;
        }
        if (controller.signal.aborted) return;
        const firstLoad = !records.current.length;
        const known = new Set(records.current.map((entry) => entry.id));
        const added = additions.filter((entry) => !known.has(entry.id));
        if (!firstLoad && added.length) setFresh((current) => new Set([...current, ...added.map((entry) => entry.id)]));
        if (forSend) lastRefresh.current = refresh;
        records.current = [...new Map([...records.current, ...additions].map((entry) => [entry.sequence, entry])).values()].sort((a, b) => a.sequence - b.sequence);
        if (firstLoad) stick.current = true;
        setMessages(records.current); changeCallback.current(next);
        setError(""); setLoading(false); if (forSend) settled.current();
        requestAnimationFrame(() => {
          if (controller.signal.aborted) return;
          // Opening a conversation whose reply is still being prepared starts that turn at the top; otherwise land on the latest message.
          if (firstLoad && next.active_task_id) anchorTurn("auto"); else followBottom();
        });
      } catch (error) { if (!controller.signal.aborted) { setError(message(error)); setLoading(false); if (forSend) settled.current(); } }
    })();
    return () => controller.abort();
  }, [session.id, refresh, reload, followBottom, anchorTurn]);
  async function loadOlder() {
    const first = records.current[0]; if (!first || first.sequence <= 1) return;
    setOlderBusy(true); setError("");
    const element = scroller();
    const height = element?.scrollHeight ?? 0;
    const top = element?.scrollTop ?? 0;
    try {
      const batch = await api<ConversationMessage[]>(`/sessions/${session.id}/messages?after_sequence=${Math.max(0, first.sequence - 101)}&limit=100`);
      records.current = [...new Map([...batch, ...records.current].map((entry) => [entry.sequence, entry])).values()].sort((a, b) => a.sequence - b.sequence);
      setMessages(records.current);
      requestAnimationFrame(() => { if (element) element.scrollTop = top + element.scrollHeight - height; });
    } catch (error) { setError(message(error)); } finally { setOlderBusy(false); }
  }
  // A message was just sent: bring it to the top of the view and let the reply build beneath it.
  useEffect(() => {
    if (!pending) return;
    const frame = requestAnimationFrame(() => anchorTurn());
    return () => cancelAnimationFrame(frame);
  }, [pending, anchorTurn]);
  const finishedRun = useCallback(() => setReload((value) => value + 1), []);
  const replyStreamed = useCallback((taskId: string) => setStreamed((current) => current.has(taskId) ? current : new Set([...current, taskId])), []);
  // The latest turn (your message and everything after it) is its own block so it can be anchored and given room.
  const lastUser = pending ? -1 : messages.findLastIndex((entry) => entry.role === "user");
  const earlier = lastUser < 0 ? messages : messages.slice(0, lastUser);
  const latest = lastUser < 0 ? [] : messages.slice(lastUser);
  const render = (entry: ConversationMessage) => {
    const when = formatDate(entry.created_at);
    if (entry.role === "user") return <article key={entry.id} aria-label="Your message" className="group/message flex min-w-0 flex-col items-end">
      <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-3xl bg-muted px-4 py-2.5 text-[15px] leading-7" title={when}>{entry.content}</div>
      <div className={actionsClass}><time dateTime={entry.created_at} className="px-1.5">{when}</time><CopyButton text={entry.content} label="Copy message" /></div>
    </article>;
    const actions = entry.task_id ? actionCount(entry.steps ?? []) : 0;
    return <article key={entry.id} aria-label="Agent response" className={`group/message min-w-0 ${fresh.has(entry.id) && !(entry.task_id && streamed.has(entry.task_id)) ? "message-in" : ""}`}>
      {entry.task_id && actions ? <ActivityDisclosure taskId={entry.task_id} count={actions} /> : null}
      <AssistantMessage content={readable(entry.content)} />
      <div className={`${actionsClass} -ml-2 mt-1`}>
        <CopyButton text={entry.content} label="Copy response" />
        {entry.task_id ? <Link href={routes.run(session.id, entry.task_id)} className="inline-flex size-8 items-center justify-center rounded-lg outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60" aria-label="View run details" title="View run details"><ArrowUpRight className="size-3.5" aria-hidden="true" /></Link> : null}
        <time dateTime={entry.created_at} className="px-1.5">{when}</time>
      </div>
    </article>;
  };
  return <div ref={root} className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-5 pb-6 pt-4 sm:px-8">
    {loading ? <div role="status" className="flex animate-pulse flex-col gap-6 motion-reduce:animate-none"><span className="sr-only">Loading conversation…</span><div className="ml-auto h-10 w-2/5 rounded-3xl bg-muted" /><div className="space-y-2.5"><div className="h-3.5 w-11/12 rounded bg-muted" /><div className="h-3.5 w-3/4 rounded bg-muted" /><div className="h-3.5 w-1/2 rounded bg-muted" /></div></div> : null}
    {messages[0]?.sequence > 1 ? <div className="text-center"><Button variant="ghost" size="sm" disabled={olderBusy} onClick={() => void loadOlder()}>{olderBusy ? "Loading earlier messages…" : "Load earlier messages"}</Button></div> : null}
    {earlier.map(render)}
    <div ref={turn} className="flex flex-col gap-6" style={reserve ? { minHeight: room } : undefined}>
      {latest.map(render)}
      {pending ? <>
        <article aria-label="Your message" className="message-in flex min-w-0 flex-col items-end"><div className="max-w-[85%] whitespace-pre-wrap break-words rounded-3xl bg-muted px-4 py-2.5 text-[15px] leading-7">{pending}</div></article>
        <ThinkingIndicator />
      </> : session.active_task_id ? <TaskTurn key={session.active_task_id} taskId={session.active_task_id} onFinished={finishedRun} onGrow={followBottom} onStreamed={replyStreamed} /> : null}
      {error ? <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-destructive"><span>{error}</span><Button variant="outline" size="sm" onClick={() => setReload((value) => value + 1)}>Try again</Button></div> : null}
      <div data-conversation-end aria-hidden="true" className="-mt-6 h-0" />
    </div>
  </div>;
}
