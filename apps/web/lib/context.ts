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
  last_run: {
    peak_input_tokens: number; model_calls: number; compacted_results: number;
    /** The main agent's newest request as the provider measured it: the live size of the model's context while `active`. Absent from an older server. */
    latest_input_tokens?: number | null;
    /** The main agent's first request of that run, measured. */
    first_input_tokens?: number | null;
    /** That run is still going on. */
    active?: boolean;
  } | null;
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
export function estimateDraftTokens(goal: string, attachments: readonly { name?: string; content: string; media_type?: string; page_count?: number; byte_size?: number }[]): number {
  const estimate = (text: string) => {
    let ascii = 0, other = 0;
    for (const character of text) {
      if (character.codePointAt(0)! < 128) ascii += 1;
      else other += 1;
    }
    return Math.ceil(ascii / 3) + other * 2;
  };
  return estimate(goal) + attachments.reduce((sum, item) => sum + estimate(item.name ?? "") + estimate(item.content) + (item.media_type === "application/pdf" ? (item.page_count ?? 20) * 32768 : item.media_type?.startsWith("image/") ? 8192 : item.media_type === "text/plain" ? Math.ceil((item.byte_size ?? 0) / 2) : 0), 0);
}

/** `measured`: the model's actual input on its latest request, while a run is going. `estimated`: the next message's starting input. */
export type ContextBasis = "measured" | "estimated";
export type ContextProjection = { used: number; percent: number; level: ContextLevel; empty: boolean; basis: ContextBasis };

/**
 * What the ring shows. While a run is going on it is the provider-measured size of the model's latest request (what the
 * model is holding right now, tool results included); the unsent draft is not part of it. Otherwise it is the estimated input
 * of the next message, with the unsent draft added so the ring moves as the person types.
 *
 * A conversation with nothing in it and nothing typed is `empty`. Previous runs' working contexts are not appended to a new
 * turn: it starts fresh from instructions and the history extract.
 */
export function projectContext(status: ContextStatus, draftTokens: number): ContextProjection {
  const windowTokens = status.input_limit_tokens ?? (status.window_tokens ? Math.max(0, status.window_tokens - (status.max_output_tokens ?? 0)) : null);
  const measured = status.last_run?.active ? status.last_run.latest_input_tokens : null;
  if (measured && measured > 0) {
    if (!windowTokens) return { used: measured, percent: 0, level: "unknown", empty: false, basis: "measured" };
    const percent = Math.min(100, Math.round((measured / windowTokens) * 1000) / 10);
    return { used: measured, percent, level: levelFor(percent), empty: false, basis: "measured" };
  }
  const empty = status.messages_total === 0 && !status.last_run && draftTokens === 0;
  // Each new turn gets fresh working context. A historical peak is never carried into its draft.
  const used = status.baseline_tokens + status.history_tokens + Math.max(0, draftTokens);
  if (!windowTokens) return { used, percent: 0, level: "unknown", empty, basis: "estimated" };
  if (empty) return { used: 0, percent: 0, level: "ok", empty, basis: "estimated" };
  const percent = Math.min(100, Math.round((used / windowTokens) * 1000) / 10);
  return { used, percent, level: levelFor(percent), empty, basis: "estimated" };
}

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
