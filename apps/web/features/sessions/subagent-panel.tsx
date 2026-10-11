"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowDown } from "lucide-react";
import { api, type TaskEvent } from "@/lib/task-api";
import { reduceActivity, createActivityState } from "@/lib/activity/reducer";
import { AssistantMessage } from "./assistant-message";
import { Timeline } from "./run-steps";
import { formatDuration } from "./timeline-model";
import { useSubagentScroll } from "./use-subagent-scroll";

type SubagentDetail = {
  run_id: string | null;
  title: string;
  assignment: string | null;
  status: string;
  summary: string | null;
  error_code: string | null;
  duration_ms: number | null;
  events: TaskEvent[];
  truncated: boolean;
};

const labels: Record<string, string> = {
  STARTING: "Starting", RUNNING: "Working", COMPLETED: "Completed", FAILED: "Couldn't finish",
  CANCELLED: "Stopped", INTERRUPTED: "Interrupted", BLOCKED: "Blocked", PARTIAL: "Partial result",
};
const isLive = (status: string) => status === "STARTING" || status === "RUNNING";

/** Fetch only while expanded. Keep the reader's scroll position and last good result across transient failures. */
export function SubagentPanel({ taskId, toolCallId, finished, showHeader = true }: { taskId: string; toolCallId: string; finished: boolean; showHeader?: boolean }) {
  const [data, setData] = useState<SubagentDetail | null>(null);
  const [error, setError] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const { viewportRef, contentRef, showLatest, jumpToLatest } = useSubagentScroll(!finished);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    async function load() {
      try {
        const detail = await api<SubagentDetail>(`/tasks/${encodeURIComponent(taskId)}/tool-calls/${encodeURIComponent(toolCallId)}/subagent`, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) });
        if (controller.signal.aborted) return;
        setData(detail);
        setError(false);
        failures = 0;
        if (isLive(detail.status)) timer = setTimeout(load, 1500);
      } catch {
        if (controller.signal.aborted) return;
        setError(true);
        if (++failures < 3) timer = setTimeout(load, 2000 * failures);
      }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [taskId, toolCallId, attempt, finished]);

  const entries = useMemo(() => {
    let state = reduceActivity(createActivityState(taskId), { type: "events", events: data?.events ?? [] });
    if (data && !isLive(data.status)) state = reduceActivity(state, { type: "terminal", status: data.status });
    return state.view;
  }, [taskId, data]);

  return <section aria-label="Subagent assignment" className="min-w-0 space-y-2 py-1 text-sm" data-subagent-panel>
    {data && showHeader ?
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="min-w-0 break-words font-medium text-foreground">{data.title}</h3>
        <p role="status" className="shrink-0 text-xs text-muted-foreground">{labels[data.status] ?? "Unavailable"}{data.duration_ms !== null ? ` · ${formatDuration(data.duration_ms)}` : ""}</p>
      </div> : null}
    <div className="relative min-w-0">
      <div ref={viewportRef} role="region" aria-label="Subagent activity and report" tabIndex={0}
        className="max-h-[min(20rem,40dvh)] min-w-0 overflow-y-auto overscroll-contain rounded-md outline-none [overflow-anchor:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden focus-visible:ring-2 focus-visible:ring-ring/60" data-subagent-scroll>
        <div ref={contentRef} className="min-w-0 space-y-3 py-0.5 pr-1">
    {data ? <>
      {data.assignment ? <details className="min-w-0 text-muted-foreground">
        <summary className="w-fit cursor-pointer rounded py-1 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 [@media(pointer:coarse)]:min-h-11">Assignment</summary>
        <p className="mt-2 whitespace-pre-wrap break-words leading-6">{data.assignment}</p>
      </details> : null}
      {entries.length ? <div className="[&_[data-activity-log]]:gap-2 [&_[data-activity-log]>ul]:space-y-1.5 [&_[data-activity-log]>ul]:text-sm"><Timeline taskId={taskId} entries={entries} animate={false} /></div> : null}
      {data.truncated ? <p className="text-xs text-muted-foreground">Showing the latest activity.</p> : null}
      {data.summary ? <div className="min-w-0 border-t pt-3">
        <p className="mb-1.5 text-xs text-muted-foreground">Report</p>
        <div className="[&>div]:space-y-3 [&>div]:text-sm [&>div]:leading-6"><AssistantMessage content={data.summary} /></div>
      </div> : <p className="text-muted-foreground">{isLive(data.status) ? "The report will appear here when the assignment finishes." : "No report was returned. The main agent can continue from the recorded activity."}</p>}
    </> : !error ? <p role="status" className="text-muted-foreground">Loading assignment…</p> : null}
    {error ? <p role="alert" className="text-muted-foreground">Couldn&apos;t refresh this assignment. <button type="button" onClick={() => setAttempt(value => value + 1)} className="rounded px-1 py-1 font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/60 [@media(pointer:coarse)]:min-h-11">Retry</button></p> : null}
        </div>
      </div>
      {showLatest ? <button type="button" onClick={jumpToLatest}
        className="absolute bottom-2 right-3 inline-flex min-h-8 items-center gap-1.5 rounded-full border bg-background px-3 text-xs text-foreground outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/60 [@media(pointer:coarse)]:min-h-11"
        aria-label="Jump to latest subagent activity"><ArrowDown className="size-3.5" aria-hidden="true" />Latest activity</button> : null}
    </div>
  </section>;
}
