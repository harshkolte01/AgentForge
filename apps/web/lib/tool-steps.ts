/**
 * Compat layer: plain-language labels for tool calls in the old three-state vocabulary. New code uses `lib/activity/*`
 * (`presentation.ts` is the source of truth; outcomes beyond running/done/failed live there).
 */
import { describeCall, groupLabel, isActionTool } from "@/lib/activity/presentation";

export type StepState = "running" | "done" | "failed";

const outcomes = { running: "running", done: "completed", failed: "failed" } as const;

/** The label for a listed tool in one of the three original states, or null for a tool that has none. */
export function labelFor(tool: string, state: StepState): string | null {
  return describeCall({ tool, outcome: outcomes[state] }, "hide")?.label ?? null;
}

/** The label for `count` finished steps of one tool, or null when a run of them is not folded into one line. */
export function pluralLabelFor(tool: string, count: number): string | null {
  return groupLabel(tool, count);
}

/** Tools worth counting as an action. Reading the person's own message and similar internals are not. */
export function actionCount(steps: readonly { tool: string }[]): number {
  return steps.filter((step) => isActionTool(step.tool)).length;
}
