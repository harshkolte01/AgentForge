import { api } from "@/lib/task-api";

/** Wire types mirror `docs/delivery.md` field for field. */
export type MergeMethod = "squash" | "merge" | "rebase";
export type UnavailableReason = "no_repository" | "sandbox_not_configured" | "github_not_connected";
export type SyncError = "github_unavailable" | "github_rate_limited" | "github_reauthorization_required" | "github_access_denied";
export type PullRequestState = "draft" | "open" | "merged" | "closed";
export type CheckStatus = "pending" | "passed" | "failed" | "neutral";
export type CheckItem = { name: string; status: CheckStatus; url: string | null; workflow: string | null };
export type ChecksState = "none" | "pending" | "passing" | "failing" | "unavailable";
export type ReviewDecision = "none" | "approved" | "changes_requested" | "review_required";
export type ReviewState = "approved" | "changes_requested" | "commented" | "dismissed" | "pending";
export type DeliveryCommit = { sha: string; message: string; files: number; additions: number; deletions: number; created_at: string; url: string };

export type PullRequest = {
  number: number; url: string; title: string;
  state: PullRequestState;
  head_sha: string;
  mergeable: "mergeable" | "conflicting" | "unknown";
  /** Commits the base has that the branch lacks. */
  behind_base: number | null;
  checks: { state: ChecksState; passed: number; failed: number; pending: number; items: CheckItem[] };
  reviews: { decision: ReviewDecision; items: { login: string; state: ReviewState }[] };
  merged_at: string | null;
  /** As allowed by the repository. */
  merge_methods: MergeMethod[];
};

export type DeliveryView = {
  available: boolean;
  unavailable_reason: UnavailableReason | null;
  capabilities: {
    writes_enabled: boolean;
    /** Push access to this repository (null = not yet checked). */
    can_write: boolean | null;
    /** The App has Checks/Statuses read (null = unknown). */
    checks_readable: boolean | null;
  };
  /** A turn is running or queued: push and discard are refused. */
  busy: boolean;
  repo_id: string | null;
  base_branch: string | null;
  base_sha: string | null;
  branch: string | null;
  branch_url: string | null;
  head_sha: string | null;
  state: "none" | "branch" | "pull_request";
  pull_request: PullRequest | null;
  commits: DeliveryCommit[];
  /** A write whose outcome is being confirmed. */
  pending_operation: { kind: string; started_at: string } | null;
  /** The last known summary of unpublished changes, readable without opening the sandbox (`/delivery/changes` is the detail). */
  unpublished: { files: number; additions: number; deletions: number; updated_at: string } | null;
  synced_at: string | null;
  sync_error: SyncError | null;
  version: number;
};

/** Why a file cannot be published: the push is refused until it is discarded. */
export type BlockedReason = "secret" | "path" | "submodule" | "symlink";
export type ChangedFile = { path: string; status: "added" | "modified" | "deleted"; additions: number; deletions: number; binary: boolean; truncated: boolean; patch: string; blocked: BlockedReason | null };
export type ChangesView = {
  workspace: "ready" | "expired" | "unavailable" | "no_repository";
  baseline: "base" | "published";
  files: ChangedFile[];
  additions: number;
  deletions: number;
  /** More files than shown. */
  truncated: boolean;
  /** Changes when the working tree does. */
  diff_version: string;
};
export type Suggestion = { commit_message: string; pr_title: string; pr_body: string; branch: string };

const root = (sessionId: string) => `/sessions/${encodeURIComponent(sessionId)}/delivery`;
function post<T>(sessionId: string, path: string, body: unknown, init: RequestInit = {}): Promise<T> {
  return api<T>(`${root(sessionId)}${path}`, { ...init, method: "POST", body: JSON.stringify(body) });
}

export const fetchDelivery = (sessionId: string, init: RequestInit = {}) => api<DeliveryView>(root(sessionId), init);
export const fetchChanges = (sessionId: string, init: RequestInit = {}) => api<ChangesView>(`${root(sessionId)}/changes`, init);
export const suggestPublication = (sessionId: string, init: RequestInit = {}) => post<Suggestion>(sessionId, "/suggest", {}, init);
/** `branch` is `Suggestion.branch`, sent back on the first publish only (once a branch exists it is ignored). */
export const pushChanges = (sessionId: string, body: { idempotency_key: string; message: string; branch?: string }) => post<DeliveryView>(sessionId, "/push", body);
export const createPullRequest = (sessionId: string, body: { idempotency_key: string; title: string; body: string; draft: boolean; message?: string; branch?: string }) => post<DeliveryView>(sessionId, "/pull-request", body);
export const markReady = (sessionId: string, idempotency_key: string) => post<DeliveryView>(sessionId, "/ready", { idempotency_key });
export const mergePullRequest = (sessionId: string, body: { idempotency_key: string; method: MergeMethod; expected_head_sha: string }) => post<DeliveryView>(sessionId, "/merge", body);
export const closePullRequest = (sessionId: string, idempotency_key: string) => post<DeliveryView>(sessionId, "/close", { idempotency_key });
export const updateBranch = (sessionId: string, idempotency_key: string) => post<DeliveryView>(sessionId, "/update-branch", { idempotency_key });
export const discardFile = (sessionId: string, path: string) => post<ChangesView>(sessionId, "/discard-file", { path });
export const refreshDelivery = (sessionId: string, init: RequestInit = {}) => post<DeliveryView>(sessionId, "/refresh", {}, init);
/** 409 `delivery_unpublished_changes` when changes would be lost and `discardUnpublished` is false: confirm with the person first. */
export const resetDelivery = (sessionId: string, idempotency_key: string, discardUnpublished = false) => post<DeliveryView>(sessionId, "/reset", { idempotency_key, discard_unpublished: discardUnpublished });
