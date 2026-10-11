"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AssistantRow, UserRow } from "@/features/sessions/message-row";
import { TaskTurn, ThinkingIndicator } from "@/features/sessions/task-turn";
import { phaseOf, rowKeyFor } from "@/features/sessions/conversation-store";
import { useConversationStore } from "@/features/sessions/use-conversation-store";
import { useScrollController } from "@/features/sessions/use-conversation-scroll";
import { pendingFor, turnRowKey, type PendingTurn, type Receipt } from "@/features/sessions/turn-flow";
import { Button } from "@/components/ui/button";
import { conversationOrder } from "@/lib/conversation-order";
import { hasEarlier } from "@/lib/message-records";
import type { ActivityEntry } from "@/lib/activity/types";
import type { ConversationMessage, ConversationSession } from "@/lib/workspace-api";

/**
 * The messages of one open conversation. Records, refreshes, history paging, the accepted-turn receipt and the hand-off from a
 * live run to its saved answer live in the conversation store; scrolling lives in the workspace's single scroll controller. This
 * component lays them out: earlier messages, then the latest turn in a block of its own that the controller can pin near the top.
 *
 * Streaming re-renders only the live turn (it keeps its own state). Saved rows are memoized on their records, which a refresh keeps
 * as the same objects when nothing changed, so history is not rebuilt per token or per poll. `onChange` and `onReceipt` must be stable.
 */
export function ConversationThread({ session, refresh, pending, receipt, onReceipt, onChange, onRetry }: { session: ConversationSession; refresh: number; pending: PendingTurn | null; receipt: Receipt | null; onReceipt: (receipt: Receipt) => void; onChange: (session: ConversationSession) => void; onRetry?: (goal: string) => void }) {
  const scroll = useScrollController();
  const { store, state } = useConversationStore(session, refresh, onChange, scroll);
  const [anchored, setAnchored] = useState(false);
  const here = pendingFor(pending, session.id);
  const phase = phaseOf(state, session.active_task_id);
  const live = phase.show ? phase.taskId : null;
  // Once a turn has been anchored its room stays, so the reply finishing never makes the page jump.
  if ((here || live) && !anchored) setAnchored(true);

  // The first read says where to land: the latest message, or the start of a turn that is still running.
  const landing = state.landing;
  useEffect(() => { if (landing) scroll.landOnLatest({ turn: landing.turn }); }, [landing, scroll]);
  // A message was just sent: bring it to the top of the view and let the reply build beneath it.
  const pendingKey = here?.key;
  useEffect(() => { if (pendingKey) scroll.beginTurn(); }, [pendingKey, scroll]);
  // The accepted turn reconciles straight from its receipt, whether or not reading the conversation afterwards works.
  const applied = useRef<Receipt | null>(null);
  useEffect(() => {
    if (!receipt || applied.current === receipt || receipt.result.session_id !== session.id) return;
    applied.current = receipt;
    store.applyReceipt(receipt);
    // Taken: a later visit to this conversation must not apply it again.
    onReceipt(receipt);
  }, [receipt, session.id, store, onReceipt]);

  const finished = useCallback((taskId: string, entries: ActivityEntry[], status: string, durationMs: number | null) => store.runFinished(taskId, entries, status, durationMs), [store]);

  const displayed = useMemo(() => conversationOrder(state.records), [state.records]);
  // Your message is pending until the server's copy of it is in the records (same row key); then it simply becomes that message.
  const waiting = !!here && !state.records.some((entry) => rowKeyFor(state, entry) === turnRowKey(here.key));
  const lastUser = waiting ? displayed.length : displayed.findLastIndex((entry) => entry.role === "user");
  const earlier = lastUser < 0 ? displayed : displayed.slice(0, lastUser);
  const latest = lastUser < 0 ? [] : displayed.slice(lastUser);
  // A failed newest reply can be asked again: the message before it is sent once more (not when it had attachments, which are not kept).
  const lastAssistant = displayed.findLast((entry) => entry.role === "assistant");
  const retryOf = (entry: ConversationMessage) => {
    if (!onRetry || entry.id !== lastAssistant?.id || entry.activity?.status !== "FAILED" || live) return undefined;
    const asked = displayed.slice(0, displayed.indexOf(entry)).findLast((item) => item.role === "user");
    return asked && !asked.attachments?.length ? () => onRetry(asked.content) : undefined;
  };
  const row = (entry: ConversationMessage) => entry.role === "user"
    ? <UserRow key={rowKeyFor(state, entry)} ledger={store.ledger} rowKey={rowKeyFor(state, entry)} content={entry.content} createdAt={entry.created_at} attachments={entry.attachments} cancelled={entry.turn_state === "cancelled" && !entry.task_id} />
    : <AssistantRow key={entry.id} entry={entry} ledger={store.ledger} ran={entry.task_id ? state.terminal.get(entry.task_id) : undefined} onRetry={retryOf(entry)} />;
  // The saved answer of the run being followed stays behind its live view; if it is already in, it sits below the activity meanwhile.
  const ofLiveRun = (entry: ConversationMessage) => entry.role === "assistant" && !!live && entry.task_id === live;
  const savedWhileLive = phase.savedReady ? latest.filter(ofLiveRun) : [];
  const showLoading = !state.loaded && !state.error && !state.records.length;
  const busy = showLoading || !!here || !!live;
  // One list for the turn's rows, so your pending message and the message the server saves for it are the same element.
  const turnRows = latest.filter((entry) => !ofLiveRun(entry)).map(row);
  if (waiting && here) turnRows.push(<UserRow key={turnRowKey(here.key)} ledger={store.ledger} rowKey={turnRowKey(here.key)} content={here.goal} attachments={here.attachments} pending />);
  const failedWait = state.savedWait?.failed ? state.savedWait : null;

  return <div data-conversation-thread aria-busy={busy} className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-5 pb-6 pt-4 sm:px-8">
    {/* One short sentence at a time ("Message sent", "Reply ready"): never the streamed text. */}
    <div role="status" aria-live="polite" aria-atomic="true" data-conversation-announcer className="sr-only">{state.announcement}</div>
    {showLoading ? <div role="status" className="flex animate-pulse flex-col gap-6 motion-reduce:animate-none"><span className="sr-only">Loading conversation…</span><div className="ml-auto h-10 w-2/5 rounded-3xl bg-muted" /><div className="space-y-2.5"><div className="h-3.5 w-11/12 rounded bg-muted" /><div className="h-3.5 w-3/4 rounded bg-muted" /><div className="h-3.5 w-1/2 rounded bg-muted" /></div></div> : null}
    {hasEarlier(state.records, state.startReached) ? <div className="text-center"><Button variant="ghost" size="sm" className="pointer-coarse:min-h-11" disabled={state.olderBusy} onClick={() => void store.loadEarlier()}>{state.olderBusy ? "Loading earlier messages…" : "Load earlier messages"}</Button></div> : null}
    {earlier.map(row)}
    <div data-conversation-turn className="flex flex-col gap-6" style={anchored ? { minHeight: "var(--conversation-room, 0px)" } : undefined}>
      {turnRows}
      {waiting && !live ? <ThinkingIndicator /> : null}
      {live ? <div data-scroll-anchor={`task:${live}`} data-live-turn={live} className="min-w-0"><TaskTurn key={live} taskId={live} savedReady={phase.savedReady} onTerminal={finished} onGrow={scroll.requestPass} onStreamed={store.replyStreamed} /></div> : null}
      {savedWhileLive.map(row)}
      {state.savedWait && !failedWait && live ? <p data-saved-wait className="text-[13px] text-muted-foreground motion-safe:animate-in motion-safe:fade-in [animation-delay:1500ms] [animation-fill-mode:both]">Finishing up…</p> : null}
      {failedWait ? <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-destructive"><span>The reply finished, but it could not be loaded{failedWait.error ? `: ${failedWait.error}` : "."}</span><Button variant="outline" size="sm" className="pointer-coarse:min-h-11" onClick={() => store.retrySaved()}>Try again</Button></div> : null}
      {state.moreAfter !== null ? <div className="flex flex-wrap items-center gap-3 text-sm text-muted-foreground"><span>Some recent messages are not loaded yet.</span><Button variant="outline" size="sm" className="pointer-coarse:min-h-11" disabled={state.newerBusy} onClick={() => void store.loadNewer()}>{state.newerBusy ? "Loading…" : "Load more"}</Button></div> : null}
      {state.error ? <div role="alert" className="flex flex-wrap items-center gap-3 text-sm text-destructive"><span>{state.error}</span><Button variant="outline" size="sm" className="pointer-coarse:min-h-11" onClick={() => void store.refresh()}>Try again</Button></div> : null}
      <div data-conversation-end aria-hidden="true" className="-mt-6 h-0" />
    </div>
  </div>;
}
