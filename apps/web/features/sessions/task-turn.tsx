"use client";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, ChevronDown, LoaderCircle, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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
    if (!active && !expanded) return;
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
        if (terminal) { stream?.close(); setConnection("Saved"); if (!notified) { notified = true; finishedCallback.current(); } }
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
  return <div className="min-w-0 space-y-4 border-y py-4">
    <div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2">{!finished ? <LoaderCircle className="size-3.5 motion-safe:animate-spin text-muted-foreground" aria-hidden="true" /> : null}<Badge variant="outline">{task ? statusLabel(task.status) : active ? "Loading task" : "Saved task"}</Badge><span className="text-xs text-muted-foreground" role="status">{task || active ? connection : ""}</span></div><div className="flex items-center gap-2">{task && !finished ? <Button variant="ghost" size="sm" disabled={busy || !!task.cancel_requested_at} onClick={() => void cancel()}><Square className="size-3" />{task.cancel_requested_at ? "Stopping…" : "Stop"}</Button> : null}<Link href={`/tasks/${taskId}`} className="inline-flex min-h-8 items-center gap-1 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2">Open task<ArrowUpRight className="size-3.5" aria-hidden="true" /></Link></div></div>
    {!finished ? <p className="text-sm leading-6 text-muted-foreground">{task?.cancel_requested_at ? "Cancellation requested. In-flight work may finish before stopping." : activity || (task?.status === "CREATED" ? "Queued for the worker. Waiting for execution to begin." : "Your agents are working. Evidence appears as it is saved.")}</p> : task?.error_code ? <p className="text-sm text-destructive">{task.error_code}</p> : null}
    {error ? <p role="alert" className="text-sm text-destructive">{error} Showing the last received state.</p> : null}
    <button type="button" className="flex min-h-8 items-center gap-2 rounded text-xs font-medium focus-visible:outline-2" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}><ChevronDown className={`size-3.5 transition-transform motion-reduce:transition-none ${expanded ? "rotate-180" : ""}`} aria-hidden="true" />{expanded ? "Hide" : "View"} plan, agent runs and evidence</button>
    {expanded ? <div className="pt-4"><TaskDetail taskId={taskId} /></div> : null}
  </div>;
}
