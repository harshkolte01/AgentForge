"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { AssistantMessage } from "@/features/sessions/assistant-message";
import { ActivityDisclosure, ActivityList } from "@/features/sessions/run-steps";
import { api, eventTypes, terminalStatuses, type Task, type TaskEvent } from "@/lib/task-api";
import { activityEventTypes, buildActivity } from "@/lib/activity";

export function ThinkingIndicator({ text = "Thinking" }: { text?: string }) {
  return <div role="status" aria-live="polite" className="py-1 text-[15px] leading-7 text-muted-foreground">
    <span className="thinking-shimmer">{text}</span>
  </div>;
}

/** Subscribes to the active run so the thread can settle the moment it finishes, and shows what the agent is doing step by step. */
export function TaskTurn({ taskId, onFinished, onGrow, onStreamed }: { taskId: string; onFinished: () => void; onGrow?: () => void; onStreamed?: (taskId: string) => void }) {
  const [task, setTask] = useState<Task | null>(null);
  const [unreachable, setUnreachable] = useState(false);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [reply, setReply] = useState<{ epoch: number; text: string }>({ epoch: 0, text: "" });
  const [finished, setFinished] = useState(false);
  const finishedCallback = useRef(onFinished);
  const streamedCallback = useRef(onStreamed);
  useEffect(() => { finishedCallback.current = onFinished; streamedCallback.current = onStreamed; }, [onFinished, onStreamed]);
  useEffect(() => {
    const controller = new AbortController();
    let stream: EventSource | undefined;
    let inFlight = false;
    let terminal = false;
    let notified = false;
    let scheduled: ReturnType<typeof setTimeout> | undefined;
    const seen = new Set<number>();
    async function load() {
      if (controller.signal.aborted || inFlight) return;
      inFlight = true;
      try {
        const result = await api<Task>(`/tasks/${taskId}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setTask(result); setUnreachable(false);
        terminal = terminalStatuses.has(result.status);
        if (terminal) setFinished(true);
        if (terminal) { stream?.close(); if (!notified) { notified = true; finishedCallback.current(); } }
      } catch { if (!controller.signal.aborted) setUnreachable(true); }
      finally { inFlight = false; }
    }
    if (typeof EventSource !== "undefined") {
      stream = new EventSource(`/api/v1/tasks/${taskId}/events?after=0`);
      const receive = (raw: Event) => {
        if (controller.signal.aborted) return;
        try {
          const event = JSON.parse((raw as MessageEvent<string>).data) as TaskEvent;
          if (event.task_id !== taskId) return;
          if (seen.has(event.sequence)) return;
          seen.add(event.sequence);
          if (activityEventTypes.has(event.event_type)) setEvents((current) => [...current, event]);
          if (!scheduled) scheduled = setTimeout(() => { scheduled = undefined; void load(); }, 300);
        } catch { /* Polling below keeps the state current. */ }
      };
      eventTypes.forEach((type) => stream?.addEventListener(type, receive));
      // The reply as the model writes it: increments of one growing text, restarted whenever a new attempt begins.
      stream.addEventListener("message.delta", (raw) => {
        if (controller.signal.aborted) return;
        try {
          const delta = JSON.parse((raw as MessageEvent<string>).data) as { task_id: string; epoch: number; offset: number; text: string };
          if (delta.task_id !== taskId) return;
          setReply((current) => {
            const base = current.epoch === delta.epoch ? current.text : "";
            if (delta.offset === 0) return { epoch: delta.epoch, text: delta.text };
            return delta.offset === base.length ? { epoch: delta.epoch, text: base + delta.text } : current;
          });
          if (delta.text) streamedCallback.current?.(taskId);
        } catch { /* The saved reply arrives when the run ends. */ }
      });
      stream.addEventListener("session.closed", () => { stream?.close(); window.dispatchEvent(new Event("agentforge-session-expired")); });
    }
    void load();
    const timer = window.setInterval(() => { if (!terminal) void load(); }, 5000);
    return () => { controller.abort(); stream?.close(); window.clearInterval(timer); if (scheduled) clearTimeout(scheduled); };
  }, [taskId]);
  const items = useMemo(() => buildActivity(events), [events]);
  const current = items.findLast((item) => item.state === "running");
  // Once the reply is streaming, the steps fold into a quiet "N actions" line and the text takes the stage.
  if (reply.text) return <div>
    <ActivityDisclosure live={items} />
    <AssistantMessage content={reply.text} streaming={!finished} onProgress={onGrow} />
    {current && !finished ? <div className="mt-3"><ActivityList items={[current]} /></div> : null}
  </div>;
  return <ActivityList items={items} live={!finished} fallback={task?.cancel_requested_at ? "Stopping" : unreachable ? "Reconnecting" : "Thinking"} limit={8} />;
}
