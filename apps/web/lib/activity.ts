/**
 * Compat layer over the activity reducer (`lib/activity/*`), kept so existing components keep compiling. New code uses the
 * reducer, selectors and presentation registry directly: they carry outcomes, groups with their calls, and exact counts.
 */
import type { ActivityEntry, TaskEvent } from "@/lib/activity/types";
import { activityEventTypes, createActivityState, reduceActivity } from "@/lib/activity/reducer";
import type { StepState } from "@/lib/tool-steps";

export { activityEventTypes };

/**
 * One line of a run's timeline: a step the agent took, a note it wrote for the person before taking it, or a heading that
 * names a change of phase ("Checking the result"). Steps have a `label` (and often a `detail`); notes have `text`. Items
 * without a `kind` are steps. Headings are only labels: they never stand for work in progress, so they never count as
 * "running" (the step under them, or the thinking line, shows what is happening now).
 */
export type ActivityItem = {
  id: string; label: string; detail?: string; state: StepState; tool?: string;
  kind?: "note" | "phase"; text?: string;
  /** How many finished steps of one tool this line stands for. */
  count?: number;
};

const stepState = (outcome: string): StepState => outcome === "running" ? "running" : outcome === "completed" ? "done" : "failed";

/** The old item ids are the sequence of the event that created the row: kept so keys and tests that rely on them still hold. */
function toItem(entry: ActivityEntry): ActivityItem {
  switch (entry.kind) {
    case "note": return { id: entry.id, kind: "note", label: "", text: entry.text, state: "done" };
    case "phase": return { id: `agent:${entry.id.slice("phase:".length)}`, kind: "phase", label: entry.label, state: entry.state === "failed" ? "failed" : entry.state };
    case "milestone": return { id: entry.id.slice("milestone:".length), label: entry.label, state: "done" };
    case "group": return { id: `tool:${entry.sequence}`, state: "done", tool: entry.tool, label: entry.label, count: entry.count };
    case "call": return { id: `tool:${entry.sequence}`, state: stepState(entry.outcome), tool: entry.tool, label: entry.label, ...(entry.detail ? { detail: entry.detail } : {}) };
  }
}

/**
 * One timeline for both the live run and a finished reply, built from the run's persisted events: the agent's notes and its
 * steps in the order they happened. Run internals (reading the request, the model starting) are left out.
 */
export function buildActivity(events: readonly TaskEvent[]): ActivityItem[] {
  const taskId = events[0]?.task_id ?? "";
  // Tools the registry does not list stay hidden here, as they always were; the reducer's default shows them generically.
  const state = reduceActivity(createActivityState(taskId, { unlistedTools: "hide" }), { type: "events", events });
  return state.view.map(toItem);
}

/** The newest live-text epoch whose words were saved as a note. Live text of that epoch or older is already in the timeline. */
export function committedEpoch(events: readonly TaskEvent[]): number {
  let latest = 0;
  for (const { event_type: type, payload } of events) {
    if (type === "assistant.message" && typeof payload.epoch === "number" && payload.epoch > latest) latest = payload.epoch;
  }
  return latest;
}

/** Whether something is happening right now: a step is running. Headings and notes are never "now". */
export function hasRunningStep(items: readonly ActivityItem[]): boolean {
  return items.some((item) => !item.kind && item.state === "running");
}

/** Whether a timeline has anything beyond internals worth showing as an expandable record. */
export function stepCount(items: readonly ActivityItem[]): number {
  return items.reduce((total, item) => total + (item.tool ? (item.count ?? 1) : 0), 0);
}
