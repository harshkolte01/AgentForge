import type { TaskEvent } from "@/lib/task-api";
import { labelFor, type StepState } from "@/lib/tool-steps";

export type ActivityItem = { id: string; label: string; detail?: string; state: StepState; tool?: string };

/** The kinds of run events that describe something the agent did. Everything else is bookkeeping. */
export const activityEventTypes = new Set(["tool.started", "tool.finished", "subagent.started", "subagent.finished", "sandbox.workspace.created", "task.repair.started"]);

const inputNouns: Record<string, string> = { goal: "your message", history: "the conversation", attachment: "an attachment" };

function agentLabel(type: string): string {
  const [namespace, ...rest] = type.split("/");
  const name = rest[rest.length - 1] ?? type;
  if (namespace === "project") return `Running “${name.replaceAll("-", " ")}”`;
  switch (name) {
    case "researcher": return "Researching";
    case "general-executor": return "Working on your request";
    case "verifier": return "Checking the result";
    case "code-executor": return "Writing code";
    case "code-reviewer": return "Reviewing the change";
    case "code-verifier": return "Verifying the change";
    default: return "Working";
  }
}

function describeTool(tool: string, state: StepState, detail: string | undefined): { label: string; detail?: string } | null {
  if (tool === "read_task_input") {
    const noun = inputNouns[detail ?? "goal"] ?? "your message";
    return { label: `${state === "running" ? "Reading" : state === "done" ? "Read" : "Couldn't read"} ${noun}` };
  }
  const label = labelFor(tool, state);
  if (!label) return null;
  return { label, detail: detail && tool === "web_search" ? `“${detail}”` : detail };
}

/** One timeline for both the live run and a finished reply, built from the run's persisted events. */
export function buildActivity(events: readonly TaskEvent[]): ActivityItem[] {
  const items: ActivityItem[] = [];
  const calls = new Map<string, { tool: string; detail?: string }>(); // item id -> the call it represents
  const text = (value: unknown) => typeof value === "string" ? value : undefined;
  for (const { event_type: type, payload, sequence } of events) {
    if (type === "tool.started") {
      const tool = text(payload.tool);
      const described = tool ? describeTool(tool, "running", text(payload.detail)) : null;
      if (!tool || !described) continue;
      const id = `tool:${sequence}`;
      calls.set(id, { tool, detail: text(payload.detail) });
      items.push({ id, state: "running", tool, ...described });
    } else if (type === "tool.finished") {
      const tool = text(payload.tool);
      if (!tool) continue;
      const state: StepState = payload.status === "ok" ? "done" : "failed";
      const index = items.findLastIndex((item) => item.state === "running" && calls.get(item.id)?.tool === tool);
      const described = describeTool(tool, state, index < 0 ? undefined : calls.get(items[index].id)?.detail);
      if (!described) continue;
      if (index < 0) items.push({ id: `tool:${sequence}`, state, tool, ...described });
      else items[index] = { id: items[index].id, state, tool, ...described };
    } else if (type === "subagent.started") {
      const kind = text(payload.type);
      if (kind) items.push({ id: `agent:${text(payload.spawn_id) ?? sequence}`, label: agentLabel(kind), state: "running" });
    } else if (type === "subagent.finished") {
      const id = `agent:${text(payload.spawn_id) ?? ""}`;
      const state: StepState = payload.status === "FAILED" || payload.status === "BLOCKED" ? "failed" : "done";
      const index = items.findIndex((item) => item.id === id);
      if (index >= 0) items[index] = { ...items[index], state };
    } else if (type === "sandbox.workspace.created") {
      items.push({ id: `workspace:${sequence}`, label: "Prepared the workspace", state: "done" });
    } else if (type === "task.repair.started") {
      items.push({ id: `repair:${sequence}`, label: "Fixing failing tests", state: "done" });
    }
  }
  return items;
}
