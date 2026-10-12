import { api } from "@/lib/task-api";

/** How much the model thinks before it answers. Which of these a model takes is the server's to say (`efforts`). */
export type ReasoningEffort = "none" | "minimal" | "low" | "medium" | "high" | "xhigh" | "max";

/** `efforts` is empty and `effort` null when the configured model offers no choice. `effort` is the one in force. */
export type ReasoningEffortChoices = { model: string | null; efforts: ReasoningEffort[]; effort: ReasoningEffort | null; default: ReasoningEffort | null };

/** The person's choice, kept on the server: it follows them to every conversation and device and applies to runs that start after it. */
export const fetchReasoningEffort = (init: RequestInit = {}) => api<ReasoningEffortChoices>("/settings/reasoning-effort", init);

export const saveReasoningEffort = (effort: ReasoningEffort) =>
  api<ReasoningEffortChoices>("/settings/reasoning-effort", { method: "PUT", body: JSON.stringify({ effort }) });
