/**
 * The pure side of tool details: what a detail response says in words, the little state machine that loads it, and a
 * per-page-session cache. Details are requested only when a row is opened (see `tool-detail.tsx`); nothing here fetches on
 * its own. Free of React so the rules are node-tested.
 */
import { describeCall } from "@/lib/activity/presentation";
import { ApiError, type ToolCallDetail, type ToolDetailText } from "@/lib/task-api";

// ---------------------------------------------------------------------------------------------------------------------
// Words

/** Why the server has nothing (more) to show, in the person's words. Unknown reasons get a generic line, never the raw code. */
const reasons: Record<string, string> = {
  not_finished: "This action hasn't finished, so there are no details yet.",
  no_output_recorded: "No output was recorded for this action.",
  evidence_unavailable: "The details of this action are no longer available.",
  artifact_missing: "The saved file for this action is no longer available.",
  artifact_unavailable: "The saved file couldn't be read right now.",
};

export function unavailableMessage(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return reasons[reason] ?? "Some details aren't available.";
}

/** Reasons that may clear up on their own, so a Retry is worth offering. */
export const retryableReasons: ReadonlySet<string> = new Set(["artifact_unavailable", "not_finished"]);

const errorWords: Record<string, string> = {
  TOOL_TIMEOUT: "The action timed out.",
  TOOL_FAILED: "The action failed.",
  UNKNOWN_REMOTE_OUTCOME: "The result of this action couldn't be confirmed.",
  SANDBOX_CONFIGURATION_REQUIRED: "Running code isn't set up for this workspace.",
  PROFILE_MISMATCH: "The requested profile doesn't match this repository.",
  SCOPE_DENIED: "This action wasn't allowed.",
  BUDGET_EXCEEDED: "The task's budget was used up.",
  TOOL_UNAVAILABLE: "The tool wasn't available.",
};
const safeCode = /^[A-Z][A-Z0-9_]{2,63}$/;

/** Safe error text for an error code: a sentence for the ones we know, the bare code for other well-formed ones, else null. */
export function errorText(code: string | null | undefined): string | null {
  if (!code || !safeCode.test(code)) return null;
  return errorWords[code] ?? `Error code: ${code}`;
}

/** "420 ms", "1.2 s", "2 min 05 s". */
export function formatDuration(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1).replace(/\.0$/, "") : Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${String(Math.round(seconds - minutes * 60)).padStart(2, "0")} s`;
}

const kb = (bytes: number) => bytes < 1024 ? `${bytes} B` : `${Math.round(bytes / 1024)} KB`;

/** The notice under a clipped preview: how much is shown and, when known, how much there is. */
export function truncationNotice(text: Pick<ToolDetailText, "truncated" | "bytes"> & { text: string }): string | null {
  if (!text.truncated) return null;
  const shown = new TextEncoder().encode(text.text).length;
  return text.bytes && text.bytes > shown ? `Truncated: showing the first ${kb(shown)} of ${kb(text.bytes)}.` : "Truncated: showing only the first part.";
}

// ---------------------------------------------------------------------------------------------------------------------
// Summaries

export type SummaryRow = { label: string; value: string };

const yesNo = (value: boolean) => value ? "Yes" : "No";

/** The rows of an execution summary: outcome first, then whatever evidence the server recorded. Absent facts are left out. */
export function executionRows(detail: ToolCallDetail): SummaryRow[] {
  const execution = detail.execution;
  const outcome = detail.outcome === "running" ? "Running" : describeCall({
    tool: detail.tool, outcome: detail.outcome, rawDetail: execution?.profile_id ?? null,
    exitCode: execution?.exit_code ?? null, timedOut: execution?.timed_out ?? null, qualified: execution?.qualified ?? null,
  })?.label ?? detail.outcome;
  const rows: SummaryRow[] = [{ label: "Outcome", value: outcome }];
  if (execution) {
    if (execution.profile_id) rows.push({ label: "Profile", value: execution.profile_id });
    if (execution.exit_code !== null) rows.push({ label: "Exit code", value: String(execution.exit_code) });
    if (execution.timed_out !== null) rows.push({ label: "Timed out", value: yesNo(execution.timed_out) });
    if (execution.qualified !== null) rows.push({ label: "Qualified result", value: yesNo(execution.qualified) });
    if (execution.observed_test_count !== null) rows.push({ label: "Tests observed", value: String(execution.observed_test_count) });
    if (execution.output_truncated) rows.push({ label: "Output", value: "Cut short by the sandbox" });
  }
  const duration = formatDuration(detail.duration_ms);
  if (duration) rows.push({ label: "Duration", value: duration });
  return rows;
}

/** "3 files changed, +40 −7". */
export function diffTotals(files: ToolCallDetail["files"]): string {
  if (!files.length) return "";
  const sum = (key: "additions" | "deletions") => files.reduce((total, file) => total + (file[key] ?? 0), 0);
  const known = files.some((file) => file.additions !== null || file.deletions !== null);
  const head = `${files.length} ${files.length === 1 ? "file" : "files"} changed`;
  return known ? `${head}, +${sum("additions")} −${sum("deletions")}` : head;
}

/** Which colour a unified-diff line gets. File headers are not additions or deletions. */
export function diffLineKind(line: string): "add" | "del" | "hunk" | "plain" {
  if (line.startsWith("+++") || line.startsWith("---")) return "plain";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  if (line.startsWith("@@")) return "hunk";
  return "plain";
}

/** A download link from a detail response, accepted only when it is this task's own artifact route. */
export function safeDownloadHref(taskId: string, url: string): string | null {
  const prefix = `/api/v1/tasks/${encodeURIComponent(taskId)}/artifacts/`;
  return url.startsWith(prefix) && /^[A-Za-z0-9_-]{1,64}$/.test(url.slice(prefix.length)) ? url : null;
}

// ---------------------------------------------------------------------------------------------------------------------
// Loading

export type DetailFailure = { kind: "unavailable" | "session" | "error"; message: string };

/** Sorts a failed detail request: nothing to show (do not retry), session gone (the app signs in), or worth another try. */
export function classifyFailure(error: unknown): DetailFailure {
  if (error instanceof ApiError) {
    if (error.status === 404) return { kind: "unavailable", message: error.message };
    if (error.status === 401) return { kind: "session", message: error.message };
    return { kind: "error", message: error.message };
  }
  return { kind: "error", message: "Couldn't load the details. Check your connection and try again." };
}

export type DetailState =
  | { status: "loading"; attempt: number }
  | { status: "ready"; detail: ToolCallDetail }
  | { status: "unavailable"; message: string }
  | { status: "error"; message: string; attempt: number };

export type DetailAction =
  | { type: "loaded"; attempt: number; detail: ToolCallDetail }
  | { type: "failed"; attempt: number; failure: DetailFailure }
  | { type: "retry" };

/** A panel opens loading (one request starts) or, if this call's details were read before, ready. */
export function initialDetailState(cached?: ToolCallDetail): DetailState {
  return cached ? { status: "ready", detail: cached } : { status: "loading", attempt: 1 };
}

/** Results of an earlier attempt (it was retried meanwhile) change nothing. */
export function detailReducer(state: DetailState, action: DetailAction): DetailState {
  switch (action.type) {
    case "loaded":
      return state.status === "loading" && state.attempt === action.attempt ? { status: "ready", detail: action.detail } : state;
    case "failed":
      if (state.status !== "loading" || state.attempt !== action.attempt) return state;
      return action.failure.kind === "error" ? { status: "error", message: action.failure.message, attempt: state.attempt } : { status: "unavailable", message: action.failure.message };
    case "retry": {
      if (state.status === "loading") return state;
      const attempt = state.status === "error" ? state.attempt + 1 : 1;
      return { status: "loading", attempt };
    }
  }
}

export type DetailFetcher = (taskId: string, toolCallId: string, init: { signal?: AbortSignal }) => Promise<ToolCallDetail>;

export type DetailLoader = {
  /** The details read earlier in this page session, if any. */
  peek(taskId: string, toolCallId: string): ToolCallDetail | undefined;
  load(taskId: string, toolCallId: string, signal?: AbortSignal): Promise<ToolCallDetail>;
  clear(): void;
};

/**
 * Loads details one call at a time and remembers them for the page session. Only complete answers are kept: a call that is
 * still running or whose preview could not be read is asked again next time, because that can change.
 */
export function createDetailLoader(fetchDetail: DetailFetcher, limit = 100): DetailLoader {
  const cache = new Map<string, ToolCallDetail>();
  const keyOf = (taskId: string, toolCallId: string) => `${taskId}:${toolCallId}`;
  return {
    peek: (taskId, toolCallId) => cache.get(keyOf(taskId, toolCallId)),
    async load(taskId, toolCallId, signal) {
      const detail = await fetchDetail(taskId, toolCallId, { signal });
      if (detail.outcome !== "running" && !detail.unavailable_reason) {
        const key = keyOf(taskId, toolCallId);
        cache.delete(key);
        cache.set(key, detail);
        while (cache.size > limit) cache.delete(cache.keys().next().value as string);
      }
      return detail;
    },
    clear: () => cache.clear(),
  };
}
