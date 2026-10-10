/** The model's context window as the composer shows it. Mirrors `contracts/context.py`; the thresholds match the server. */
export type ContextLevel = "ok" | "notice" | "high" | "unknown";
export type ContextStatus = {
  model: string | null;
  provider: string | null;
  window_tokens: number | null;
  max_output_tokens: number | null;
  level: ContextLevel;
  percent: number;
  used_tokens: number;
  /** What `used_tokens` measures: the next turn's starting context, or the latest run's measured peak. */
  used_basis: "next_turn" | "last_run_peak";
  baseline_tokens: number;
  history_tokens: number;
  history_budget_tokens: number;
  last_run: { peak_input_tokens: number; model_calls: number; compacted_results: number } | null;
  messages_total: number;
  messages_in_full: number;
  messages_condensed: number;
  messages_omitted: number;
};

export const NOTICE_PERCENT = 50;
export const HIGH_PERCENT = 80;
const CHARS_PER_TOKEN = 4;

export function levelFor(percent: number): Exclude<ContextLevel, "unknown"> {
  return percent >= HIGH_PERCENT ? "high" : percent >= NOTICE_PERCENT ? "notice" : "ok";
}

/** 842 -> "842", 25973 -> "26K", 262144 -> "262K", 1048576 -> "1.0M". */
export function formatTokens(count: number): string {
  if (count < 1000) return String(Math.max(0, Math.round(count)));
  if (count < 100_000) return `${(count / 1000).toFixed(1).replace(/\.0$/, "")}K`;
  if (count < 1_000_000) return `${Math.round(count / 1000)}K`;
  return `${(count / 1_000_000).toFixed(1)}M`;
}

/** Characters in what the person has typed or attached, as a rough token count (about four characters each). */
export function estimateDraftTokens(goal: string, attachments: readonly { content: string }[]): number {
  const characters = goal.length + attachments.reduce((sum, item) => sum + item.content.length, 0);
  return Math.ceil(characters / CHARS_PER_TOKEN);
}

export type ContextProjection = { used: number; percent: number; level: ContextLevel; empty: boolean };

/**
 * The status with the unsent draft added, so the ring moves as the person types.
 *
 * A conversation with nothing in it and nothing typed is `empty`: the fixed instructions and tool schemas
 * are only sent with a message, so they are not counted against a window nobody has used yet.
 */
export function projectContext(status: ContextStatus, draftTokens: number): ContextProjection {
  const empty = status.messages_total === 0 && !status.last_run && draftTokens === 0;
  const used = status.used_tokens + draftTokens;
  const windowTokens = status.window_tokens;
  if (!windowTokens) return { used, percent: 0, level: "unknown", empty };
  if (empty) return { used: 0, percent: 0, level: "ok", empty };
  const percent = Math.min(100, Math.round((used / windowTokens) * 1000) / 10);
  return { used, percent, level: levelFor(percent), empty };
}

export const levelText: Record<ContextLevel, string> = {
  ok: "Plenty of room",
  notice: "Filling up",
  high: "Nearly full",
  unknown: "Size not configured",
};

/** How much of the conversation the agent reads in full, as one plain sentence. */
export function coverageText(status: ContextStatus): string {
  const { messages_total: total, messages_in_full: full, messages_condensed: condensed, messages_omitted: omitted } = status;
  if (total === 0) return "No messages yet.";
  const noun = total === 1 ? "message" : "messages";
  if (!condensed && !omitted) return `${total} ${noun}, all read in full.`;
  const parts = [`${full} in full`];
  if (condensed) parts.push(`${condensed} condensed`);
  if (omitted) parts.push(`${omitted} omitted`);
  return `${total} ${noun}: ${parts.join(", ")}.`;
}
