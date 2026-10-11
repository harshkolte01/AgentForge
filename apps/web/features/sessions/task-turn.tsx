"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { AssistantMessage } from "@/features/sessions/assistant-message";
import { splitAssistantContent } from "@/features/sessions/assistant-content";
import { Timeline } from "@/features/sessions/run-steps";
import { activityAnnouncement, endEntries, heldAsLive, runDuration, summarizeEntries, syncNotice } from "@/features/sessions/timeline-model";
import { useTaskSync } from "@/features/sessions/use-task-sync";
import { pendingApprovals } from "@/lib/activity/approvals";
import { visibleLive } from "@/lib/activity/live-text";
import { isSettled, selectCommittedEpoch, selectEntries } from "@/lib/activity/reducer";
import type { ActivityEntry } from "@/lib/activity/types";

/** The line shown from the moment a message is sent until the run reports anything: the same one the run view starts with. */
export function ThinkingIndicator({ text = "Thinking" }: { text?: string }) {
  return <Timeline entries={[]} live fallback={text} />;
}

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
export function TaskTurn({ taskId, savedReady, onTerminal, onGrow, onStreamed }: {
  taskId: string;
  savedReady: boolean;
  onTerminal: (taskId: string, entries: ActivityEntry[], status: string, durationMs: number | null) => void;
  onGrow?: () => void;
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
  const shown = held && !sync.live.text ? heldAsLive(held) : sync.live;
  const live = visibleLive(shown, selectCommittedEpoch(activity), savedReady ? "saved" : null);
  const summary = !live && over && !savedReady && task?.final_summary && answeredStatuses.has(task.status) ? task.final_summary : "";
  const raw = live || summary;
  const tailText = raw && splitAssistantContent(raw, !over).answer ? raw : "";
  const answering = !!tailText && (!!summary || shown.kind === "answer");

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
  useEffect(() => { terminalCallback.current = onTerminal; streamedCallback.current = onStreamed; growCallback.current = onGrow; }, [onTerminal, onStreamed, onGrow]);

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

  return <Timeline taskId={taskId} entries={entries} live={!over} quiet={quiet} notice={notice} onRetry={retry} stopping={stopping} announcement={announcement} fold={over && (actions > 0 || notes > 0)} durationMs={durationMs} approvals={approvals}
    tail={tailText ? <AssistantMessage content={tailText} streaming={!over} onProgress={onGrow} /> : null} />;
}
