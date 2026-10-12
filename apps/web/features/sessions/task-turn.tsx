"use client";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { AssistantMessage } from "@/features/sessions/assistant-message";
import { splitAssistantContent } from "@/features/sessions/assistant-content";
import { Timeline } from "@/features/sessions/run-steps";
import { activityAnnouncement, endEntries, heldAsLive, runDuration, summarizeEntries, syncNotice } from "@/features/sessions/timeline-model";
import { useTaskSync } from "@/features/sessions/use-task-sync";
import { pendingApprovals } from "@/lib/activity/approvals";
import { emptyLive, isToolKind, liveToolOf, visibleLive } from "@/lib/activity/live-text";
import { isSettled, selectCommittedEpoch, selectEntries } from "@/lib/activity/reducer";
import type { ActivityEntry } from "@/lib/activity/types";

/**
 * The line shown from the moment a message is sent until the run reports anything: the same one the run view starts with. It
 * enters once per sent message (`turnKey`), so a view that takes over the same turn shows it already in place.
 */
export function ThinkingIndicator({ text = "Thinking", turnKey }: { text?: string; turnKey?: string }) {
  const [enters] = useState(() => !turnKey || !thinkingShown.has(turnKey));
  useEffect(() => { if (turnKey) thinkingShown.add(turnKey); }, [turnKey]);
  return <Timeline entries={[]} live fallback={text} statusEnters={enters} />;
}
const thinkingShown = new Set<string>();

/** How long a run may show nothing new before a "Thinking" line appears under what it has already written. */
const QUIET_MS = 2000;
/** Screen-reader updates are spaced out so a busy run does not read out every action; the end of a run is said at once. */
const ANNOUNCE_MS = 1200;
/** Statuses whose summary is the answer itself, for the moment between the live text being cleared and the saved message. */
const answeredStatuses = new Set(["COMPLETED", "PARTIAL"]);

/** True once nothing new has shown for `QUIET_MS` since `since` (a clock time). Re-arms itself whenever `since` moves. */
function useQuiet(since: number, active: boolean): boolean {
  const [quietSince, setQuietSince] = useState(0);
  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(() => setQuietSince(since), Math.max(0, since + QUIET_MS - Date.now()));
    return () => clearTimeout(timer);
  }, [since, active]);
  return active && quietSince === since;
}

/** The text a screen reader should hear, applied after a short pause (or at once for the end of a run). */
function useAnnouncement(text: string, immediate: boolean): string {
  const [announced, setAnnounced] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setAnnounced(text), immediate ? 0 : ANNOUNCE_MS);
    return () => clearTimeout(timer);
  }, [text, immediate]);
  return announced;
}

/**
 * Follows the active run and shows it as it happens, like a transcript: what the agent says it will do, each step the moment it
 * starts, and the answer as it is written. The recovery controller (`lib/activity/sync.ts`) keeps events and live text in step
 * with the server; this component only renders its snapshots.
 *
 * Hand-off to the saved message: the live text stays on screen until `savedReady` says the thread holds the saved answer, then the
 * tail renders nothing in that same commit, so there is no blank frame and no second copy. `onTerminal` fires once, only after
 * every event up to the task's end has been read, with the entries to keep in place.
 */
export function TaskTurn({ taskId, savedReady, holdStatus = true, onTerminal, onGrow, onShift, onStreamed }: {
  taskId: string;
  savedReady: boolean;
  /**
   * A run that ended before any of its answer was shown keeps its "Thinking" line until the saved answer replaces it, so the turn
   * never goes blank in between. False once the thread says something of its own about the wait ("Finishing up", a failure).
   */
  holdStatus?: boolean;
  onTerminal: (taskId: string, entries: ActivityEntry[], status: string, durationMs: number | null) => void;
  onGrow?: () => void;
  /** The steps above the answer just folded away by `px` (negative): the view should move with them. */
  onShift?: (px: number) => void;
  onStreamed?: (taskId: string) => void;
}) {
  const { sync, held, retry } = useTaskSync(taskId, savedReady);
  const { activity, task } = sync;
  const over = sync.terminal || !!activity.terminal;
  const status = activity.terminal?.status ?? task?.status ?? null;

  // A task known to be over never shows a spinning step, even while its last events are still being fetched.
  const view = selectEntries(activity);
  const entries = useMemo(() => over ? endEntries(view) : view, [over, view]);

  // Live text: shown unchanged until its durable copy exists (a saved note of its epoch, or the saved answer), never twice. The
  // text held through the end of the run stands in when the server clears the live copy, so the tail never blanks before the
  // saved message takes over.
  // A tool call the live row holds (written by the model, or running) is not words for the person: the timeline shows it as a step.
  const prose = isToolKind(sync.live.kind) ? emptyLive : sync.live;
  const liveTool = over ? null : liveToolOf(sync.live);
  const shown = held && !prose.text ? heldAsLive(held) : prose;
  const live = visibleLive(shown, selectCommittedEpoch(activity), savedReady ? "saved" : null);
  const summary = !live && over && !savedReady && task?.final_summary && answeredStatuses.has(task.status) ? task.final_summary : "";
  const raw = live || summary;
  const tailText = raw && splitAssistantContent(raw, !over).answer ? raw : "";
  // With tools offered the answer streams as plain progress text and is only called the answer when the call ends; text still
  // on screen when the run is over is the answer either way, so its saved copy takes over without a second entrance.
  const answering = !!tailText && (!!summary || shown.kind === "answer" || over);

  const stopping = !!task?.cancel_requested_at && !over;
  const notice = useMemo(() => syncNotice(sync), [sync]);
  const quiet = useQuiet(sync.lastProgressAt, !over);
  const { actions, notes } = useMemo(() => summarizeEntries(entries), [entries]);
  const durationMs = useMemo(() => over ? runDuration(activity.events) : null, [over, activity.events]);
  const announcement = useAnnouncement(activityAnnouncement({ notice, ended: over ? status ?? "FINISHED" : null, stopping, actions }), over);

  // Callbacks are read through refs so a parent re-render never restarts an effect.
  const terminalCallback = useRef(onTerminal);
  const streamedCallback = useRef(onStreamed);
  const growCallback = useRef(onGrow);
  const shiftCallback = useRef(onShift);
  useEffect(() => { terminalCallback.current = onTerminal; streamedCallback.current = onStreamed; growCallback.current = onGrow; shiftCallback.current = onShift; }, [onTerminal, onStreamed, onGrow, onShift]);

  // When the run ends its steps fold into one "Worked · 41s" line and the answer below them moves up, often by hundreds of
  // pixels. The answer's offset within the turn is noted at every commit until then; on the commit that folds, before paint, the
  // view is moved by exactly the difference, so the answer the reader is looking at stays still. A fold wholly below the view
  // moves nothing the reader sees and is left alone.
  // Only an answer on screen may take the steps' place early. Without one (it was written too fast to stream), the steps stay
  // until the saved message replaces the whole turn in one commit: folding first would collapse the turn to a single line and
  // then grow it again when the answer lands.
  const fold = over && (actions > 0 || notes > 0) && !!tailText;
  const tail = useRef<HTMLDivElement>(null);
  const tailOffset = useRef<number | null>(null);
  const folded = useRef(false);
  useLayoutEffect(() => {
    if (folded.current) return;
    const element = tail.current;
    const turn = element?.closest("[data-live-turn]");
    const offset = element && turn ? element.getBoundingClientRect().top - turn.getBoundingClientRect().top : null;
    const before = tailOffset.current;
    tailOffset.current = offset;
    if (!fold) return;
    folded.current = true;
    const scroller = element?.closest("[data-conversation-scroll]");
    if (!element || !scroller || offset === null || before === null) return;
    const delta = offset - before;
    if (element.getBoundingClientRect().top - delta < scroller.getBoundingClientRect().bottom) shiftCallback.current?.(delta);
  });

  const settled = over && isSettled(activity) && !!status;
  const notified = useRef<string | null>(null);
  useEffect(() => {
    if (!settled || notified.current === taskId || !status) return;
    notified.current = taskId;
    terminalCallback.current(taskId, entries.slice(), status, durationMs);
  }, [settled, status, taskId, entries, durationMs]);

  const streamed = useRef<string | null>(null);
  useEffect(() => {
    if (!answering || streamed.current === taskId) return;
    streamed.current = taskId;
    streamedCallback.current?.(taskId);
  }, [answering, taskId]);

  // Calls the agent stopped before and is waiting on the person for. Nothing waits once the run is over.
  const events = activity.events;
  const approvals = useMemo(() => over ? [] : pendingApprovals(events), [over, events]);

  const grown = entries.length + approvals.length + (notice ? 1 : 0);
  useEffect(() => { if (grown) growCallback.current?.(); }, [grown]);
  // A call being written or printing grows its row as its text arrives: the thread follows it like it follows the reply.
  const liveToolId = liveTool?.callId;
  const liveToolText = liveTool?.text;
  useEffect(() => { if (liveToolId) growCallback.current?.(); }, [liveToolId, liveToolText]);

  // Over, with no answer on screen yet and the saved one not in: the status line stays (it only shows when no steps do).
  const holding = over && !savedReady && !tailText && holdStatus;
  return <Timeline taskId={taskId} entries={entries} live={!over || holding} quiet={quiet} notice={notice} onRetry={retry} stopping={stopping} announcement={announcement} fold={fold} durationMs={durationMs} approvals={approvals} liveTool={liveTool} collapse
    tail={tailText ? <div ref={tail} className="min-w-0"><AssistantMessage content={tailText} streaming={!over} onProgress={onGrow} /></div> : null} />;
}
