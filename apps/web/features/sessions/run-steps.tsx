"use client";
import { useState } from "react";
import { Check, ChevronRight, TriangleAlert } from "lucide-react";
import { api, message, type TaskEvent } from "@/lib/task-api";
import { activityEventTypes, buildActivity, type ActivityItem } from "@/lib/activity";
import { actionCount } from "@/lib/tool-steps";

/** Only the newest running step animates; a running parent phase (for example "Researching") stays calm beneath it. */
function Row({ item, current }: { item: ActivityItem; current: boolean }) {
  return <li className="message-in flex min-w-0 items-center gap-2.5">
    <span className="flex size-4 shrink-0 items-center justify-center" aria-hidden="true">
      {item.state === "running" ? (current ? <span className="thinking-orb" /> : <span className="size-1.5 rounded-full bg-current opacity-50" />) : item.state === "failed" ? <TriangleAlert className="size-3.5" /> : <Check className="size-3.5" />}
    </span>
    <span className="min-w-0 truncate">
      {item.state === "running" && current ? <span className="thinking-shimmer">{item.label}</span> : item.label}
      {item.detail ? <span className="ml-1.5 opacity-70">{item.detail}</span> : null}
    </span>
  </li>;
}

/** What the agent is doing or did, one line per action, oldest first. While live it always ends with a current step. */
export function ActivityList({ items, live = false, fallback = "Thinking", limit }: { items: ActivityItem[]; live?: boolean; fallback?: string; limit?: number }) {
  const hasRunning = items.some((item) => item.state === "running");
  const rows: ActivityItem[] = live && !hasRunning ? [...items, { id: "current", label: fallback, state: "running" }] : items;
  const currentId = rows.findLast((item) => item.state === "running")?.id;
  const hidden = limit && rows.length > limit ? rows.length - limit : 0;
  const visible = hidden ? rows.slice(-limit!) : rows;
  return <div role="status" aria-live="polite" className="text-[15px] leading-6 text-muted-foreground">
    {hidden ? <p className="mb-2 pl-[26px] text-sm opacity-70">{hidden} earlier {hidden === 1 ? "step" : "steps"}</p> : null}
    <ul className="space-y-2" aria-label="Activity">{visible.map((item) => <Row key={item.id} item={item} current={item.id === currentId} />)}</ul>
  </div>;
}

/**
 * Collapsed summary above a reply. Given `live` items it shows them as they grow (while the reply streams);
 * given a taskId it loads the finished run's events when opened. Both show the same timeline.
 */
export function ActivityDisclosure({ taskId, count, live }: { taskId?: string; count?: number; live?: ActivityItem[] }) {
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<ActivityItem[] | null>(null);
  const [error, setError] = useState("");
  const items = live ?? loaded;
  const total = live ? live.filter((item) => item.tool && actionCount([{ tool: item.tool }])).length : (count ?? 0);
  if (!total) return null;
  async function toggle() {
    const next = !open;
    setOpen(next);
    if (!next || items || !taskId) return;
    setError("");
    try {
      const events: TaskEvent[] = [];
      let after = 0;
      for (let page = 0; page < 5; page++) {
        const batch = await api<TaskEvent[]>(`/tasks/${taskId}/events/history?after=${after}&limit=100`);
        events.push(...batch.filter((event) => activityEventTypes.has(event.event_type)));
        if (batch.length < 100) break;
        after = batch[batch.length - 1].sequence;
      }
      setLoaded(buildActivity(events));
    } catch (error) { setError(message(error)); }
  }
  return <div className="mb-3">
    <button type="button" aria-expanded={open} className="-ml-1 inline-flex min-h-8 items-center gap-1 rounded-lg px-1 text-[13px] text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 motion-reduce:transition-none" onClick={() => void toggle()}>
      <ChevronRight className={`size-3.5 transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`} aria-hidden="true" />
      {total === 1 ? "1 action" : `${total} actions`}
    </button>
    {open ? <div className="mt-1.5 ml-1.5 border-l pl-4 text-sm">
      {error ? <p role="alert" className="text-destructive">{error}</p> : items ? (items.length ? <ActivityList items={items} /> : <p className="text-muted-foreground">No recorded activity.</p>) : <p role="status" className="text-muted-foreground">Loading…</p>}
    </div> : null}
  </div>;
}
