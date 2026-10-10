"use client";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, ChevronDown, LoaderCircle, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
const TaskDetail = dynamic(() => import("@/components/task-detail").then((module) => module.TaskDetail), { loading: () => <p role="status" className="text-sm text-muted-foreground">Loading execution evidence…</p> });
import { api, eventTypes, message, statusLabel, terminalStatuses, type Task, type TaskEvent } from "@/lib/task-api";

export function TaskTurn({ taskId, onFinished, active }: { taskId: string; active: boolean; onFinished: () => void }) {
  const [task, setTask] = useState<Task | null>(null);
  const [error, setError] = useState("");
  const [activity, setActivity] = useState("");
  const [expanded, setExpanded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [connection, setConnection] = useState("Connecting");
  const finishedCallback = useRef(onFinished);
  useEffect(() => { finishedCallback.current = onFinished; }, [onFinished]);
  useEffect(() => {
    const controller = new AbortController();
    let stream: EventSource | undefined;
    let inFlight = false;
    let terminal = false;
    let notified = false;
    let scheduled: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      if (controller.signal.aborted || inFlight) return;
      inFlight = true;
      try {
        const result = await api<Task>(`/tasks/${taskId}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setTask(result); setError("");
        terminal = terminalStatuses.has(result.status);
        if (terminal) { stream?.close(); setConnection("Saved"); if (active && !notified) { notified = true; finishedCallback.current(); } }
      } catch (error) { if (!controller.signal.aborted) setError(message(error)); }
      finally { inFlight = false; }
    }
    if (active && typeof EventSource !== "undefined") {
      stream = new EventSource(`/api/v1/tasks/${taskId}/events?after=0`);
      stream.onopen = () => setConnection("Live");
      stream.onerror = () => { if (!terminal && !controller.signal.aborted) setConnection("Reconnecting · polling"); };
      const receive = (raw: Event) => {
        if (controller.signal.aborted) return;
        try {
          const event = JSON.parse((raw as MessageEvent<string>).data) as TaskEvent;
          if (event.task_id !== taskId) return;
          setActivity(typeof event.payload.tool === "string" ? event.payload.tool.replaceAll("_", " ") : event.event_type.split(".").map(statusLabel).join(" · "));
          if (!scheduled) scheduled = setTimeout(() => { scheduled = undefined; void load(); }, 300);
        } catch { setConnection("Polling"); }
      };
      eventTypes.forEach((type) => stream?.addEventListener(type, receive));
      stream.addEventListener("session.closed", () => { stream?.close(); window.dispatchEvent(new Event("agentforge-session-expired")); });
    }
    void load();
    const timer = window.setInterval(() => { if (!terminal) void load(); }, 5000);
    return () => { controller.abort(); stream?.close(); window.clearInterval(timer); if (scheduled) clearTimeout(scheduled); };
  }, [taskId, active, expanded]);
  const finished = task ? terminalStatuses.has(task.status) : !active;
  async function cancel() {
    setBusy(true); setError("");
    try { setTask(await api<Task>(`/tasks/${taskId}/cancel`, { method: "POST" })); }
    catch (error) { setError(message(error)); } finally { setBusy(false); }
  }
  if (finished && task?.final_result?.conversation === true && !error) return null;
  return <div className="min-w-0 py-1">
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      {!finished ? <LoaderCircle className="size-3 motion-safe:animate-spin" aria-hidden="true" /> : null}
      <span role="status">{task ? statusLabel(task.status) : error ? "Status unavailable" : "Loading status…"}</span>
      {active && !finished ? <span className="text-[11px]">{connection}</span> : null}
      <button type="button" className="inline-flex min-h-11 items-center gap-1 rounded text-xs hover:text-foreground focus-visible:outline-2 sm:min-h-8" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}><ChevronDown className={`size-3 transition-transform motion-reduce:transition-none ${expanded ? "rotate-180" : ""}`} aria-hidden="true" />Run details</button>
      <div className="ml-auto flex items-center gap-2">{task && !finished ? <Button variant="ghost" size="sm" className="min-h-11 sm:min-h-8" disabled={busy || !!task.cancel_requested_at} onClick={() => void cancel()}><Square className="size-3" />{task.cancel_requested_at ? "Stopping…" : "Stop"}</Button> : null}<Link href={`/tasks/${taskId}`} className="inline-flex min-h-11 items-center gap-1 rounded text-xs hover:text-foreground focus-visible:outline-2 sm:min-h-8" aria-label="Open full run details"><ArrowUpRight className="size-3.5" aria-hidden="true" /></Link></div>
    </div>
    {!finished ? <p className="text-xs leading-5 text-muted-foreground">{task?.cancel_requested_at ? "Stopping after in-flight work finishes." : activity || (task?.status === "CREATED" ? "Waiting to start…" : "Working…")}</p> : task?.error_code ? <p className="text-xs leading-5 text-destructive">This run could not finish. Review run details for more information.</p> : null}
    {error ? <p role="alert" className="text-xs leading-5 text-destructive">{error}{task ? " Showing the last received state." : " Open run details to retry."}</p> : null}
    {expanded ? <div className="mt-3 border-t pt-4"><TaskDetail taskId={taskId} /></div> : null}
  </div>;
}
