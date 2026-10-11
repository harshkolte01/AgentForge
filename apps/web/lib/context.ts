/** The model's context window as the composer shows it. Mirrors `contracts/context.py`; the thresholds match the server. */
export type ContextLevel = "ok" | "notice" | "high" | "unknown";
export type ContextStatus = {
  model: string | null;
  provider: string | null;
  window_tokens: number | null;
  max_output_tokens: number | null;
  input_limit_tokens?: number | null;
  level: ContextLevel;
  percent: number;
  used_tokens: number;
  /** New responses use next_turn. The historical variant is accepted during rolling upgrades. */
  used_basis: "next_turn" | "last_run_peak";
  baseline_tokens: number;
  history_tokens: number;
  history_budget_tokens: number;
  summary_through_ordinal?: number;
  uncovered_turns?: number;
  retrieval_available?: boolean;
  snapshot_revision?: number;
  last_run: { peak_input_tokens: number; model_calls: number; compacted_results: number } | null;
  messages_total: number;
  messages_in_full: number;
  messages_condensed: number;
  messages_omitted: number;
};

export const NOTICE_PERCENT = 50;
export const HIGH_PERCENT = 80;

export function levelFor(percent: number): Exclude<ContextLevel, "unknown"> {
  return percent >= HIGH_PERCENT ? "high" : percent >= NOTICE_PERCENT ? "notice" : "ok";
}

/** 842 -> "842", 25973 -> "26K", 262144 -> "262K", 1048576 -> "1.0M". */
export function formatTokens(count: number): string {
  if (!Number.isFinite(count)) return "—";
  if (count < 1000) return String(Math.max(0, Math.round(count)));
  if (count < 100_000) return `${(count / 1000).toFixed(1).replace(/\.0$/, "")}K`;
  if (count < 1_000_000) return `${Math.round(count / 1000)}K`;
  return `${Number((count / 1_000_000).toFixed(2))}M`;
}

/** Same text estimate as the harness: three ASCII characters per token, two per Unicode code point. */
export function estimateDraftTokens(goal: string, attachments: readonly { name?: string; content: string }[]): number {
  const estimate = (text: string) => {
    let ascii = 0, other = 0;
    for (const character of text) {
      if (character.codePointAt(0)! < 128) ascii += 1;
      else other += 1;
    }
    return Math.ceil(ascii / 3) + other * 2;
  };
  return estimate(goal) + attachments.reduce((sum, item) => sum + estimate(item.name ?? "") + estimate(item.content), 0);
}

export type ContextProjection = { used: number; percent: number; level: ContextLevel; empty: boolean };

/**
 * The status with the unsent draft added, so the ring moves as the person types.
 *
 * A conversation with nothing in it and nothing typed is `empty`; otherwise instructions, history and
 * the draft estimate the next input. Previous runs' working contexts are not appended to a new turn.
 */
export function projectContext(status: ContextStatus, draftTokens: number): ContextProjection {
  const empty = status.messages_total === 0 && !status.last_run && draftTokens === 0;
  // Each new turn gets fresh working context. A historical peak is never carried into its draft.
  const used = status.baseline_tokens + status.history_tokens + Math.max(0, draftTokens);
  const windowTokens = status.input_limit_tokens ?? (status.window_tokens ? Math.max(0, status.window_tokens - (status.max_output_tokens ?? 0)) : null);
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

/** What the next turn's history extract includes; this is not a proof that a model has read it. */
export function coverageText(status: ContextStatus): string {
  const { messages_total: total, messages_in_full: full, messages_condensed: condensed, messages_omitted: omitted } = status;
  if (total === 0) return "No messages yet.";
  const noun = total === 1 ? "message" : "messages";
  if (!condensed && !omitted) return `${total} ${noun} included in full.`;
  const parts = [`${full} in full`];
  if (condensed) parts.push(`${condensed} condensed`);
  if (omitted) parts.push(`${omitted} omitted`);
  return `${total} ${noun}: ${parts.join(", ")}.`;
}
