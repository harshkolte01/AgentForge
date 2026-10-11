import { ApiError, message } from "@/lib/task-api";
import type { SyncError } from "@/lib/delivery-api";

/** Where a person re-authorizes GitHub. */
export const RECONNECT_HREF = "/api/v1/auth/github/start";

/** Every machine code the delivery routes can return (`docs/delivery.md`), as one sentence a person can act on. */
const sentences: Record<string, string> = {
  agent_running: "The agent is working. Wait for it to finish, then try again.",
  nothing_to_publish: "There is nothing new to publish: the workspace matches what is already on GitHub.",
  delivery_workspace_expired: "This conversation's sandbox has expired, so its unpublished edits are gone. Ask the agent to make them again.",
  delivery_no_repository: "This conversation has no repository to publish to.",
  github_branch_moved: "The branch changed on GitHub since AgentForge last pushed to it. Refresh to see where it is now.",
  github_base_branch_changed: "The base branch changed on GitHub since this work began. Refresh and try again.",
  github_publish_branch_exists: "A branch with this name already exists on GitHub. It may be an earlier attempt that did go through, so refresh first.",
  github_not_mergeable: "GitHub says this pull request can't be merged yet. Check its checks, reviews and conflicts, then try again.",
  github_draft_pull_request: "This pull request is still a draft. Mark it ready for review first.",
  github_write_permission_required: "AgentForge can't write to this repository. Check that the GitHub App has Contents and Pull requests write access here.",
  github_writes_disabled: "Publishing is turned off on this server.",
  github_reauthorization_required: "Your GitHub authorization has expired. Reconnect GitHub to continue.",
  github_rate_limited: "GitHub is limiting requests right now. Wait a minute and try again.",
  github_unavailable: "GitHub is unavailable right now. Try again in a moment.",
  github_access_denied: "GitHub denied access to this repository. Review the app installation and your permissions.",
  delivery_operation_in_progress: "Another change to this delivery is still in progress. Wait a moment and try again.",
  delivery_outcome_unknown: "GitHub didn't confirm whether that went through. The status was refreshed: check it before trying again.",
  github_branch_missing: "The branch was deleted on GitHub, and its earlier commits can't be restored from here. Start again from a fresh branch.",
  github_change_too_large: "This change is too large to publish in one go (over 500 files or 20 MB). Split the work into smaller steps.",
  delivery_not_published: "Nothing has been published yet, so there is no pull request to act on.",
  github_not_connected: "GitHub isn't connected. Connect it in Settings to publish.",
  delivery_sandbox_not_configured: "This server has no coding sandbox set up, so there is no workspace to publish from.",
  delivery_message_required: "Write a commit message before publishing.",
  delivery_branch_invalid: "That branch name isn't valid. Use letters, numbers, dashes and slashes.",
  delivery_workspace_unavailable: "The sandbox didn't answer. Try again in a moment.",
  github_unsupported_change: "These changes include something that can't be published, such as a symlink, a submodule or a file mode change. Discard those files and try again.",
  github_unsafe_change_path: "A changed file has a path that isn't safe to publish. Discard it and try again.",
  delivery_unpublished_changes: "There are changes in the workspace that haven't been published. Starting over would discard them, so it needs your confirmation.",
  delivery_reset_not_allowed: "Starting over isn't possible while a pull request is still open. Merge or close it first.",
  delivery_workspace_changed: "The files changed while they were being published, so nothing was sent. Review the changes and try again.",
};

/** These codes carry the paths they are about: `github_path_conflict:a.ts,b.ts`. */
const conflict = "github_path_conflict";
const blocked = "delivery_blocked_paths";
const splitCode = (code: string) => { const at = code.indexOf(":"); return at < 0 ? [code, ""] as const : [code.slice(0, at), code.slice(at + 1)] as const; };

/** The code without any payload (`github_path_conflict:a,b` → `github_path_conflict`). */
export function errorCode(error: unknown): string | undefined {
  return error instanceof ApiError && error.code ? splitCode(error.code)[0] : undefined;
}
/** The paths a `github_path_conflict` or `delivery_blocked_paths` names. */
export function errorPaths(error: unknown): string[] {
  if (!(error instanceof ApiError) || !error.code) return [];
  const [code, rest] = splitCode(error.code);
  return code === conflict || code === blocked ? rest.split(",").map((path) => path.trim()).filter(Boolean) : [];
}

const listed = (paths: string[]) => paths.slice(0, 3).join(", ") + (paths.length > 3 ? ` and ${paths.length - 3} more` : "");

export function deliveryMessage(error: unknown): string {
  const code = errorCode(error);
  if (code === conflict) {
    const paths = errorPaths(error);
    return `${paths.length ? `${listed(paths)} changed` : "Files changed"} on GitHub since the last push, so nothing was pushed. Update the branch from GitHub or ask the agent to reconcile them.`;
  }
  if (code === blocked) {
    const paths = errorPaths(error);
    return `${paths.length ? `${listed(paths)} can't be published` : "Some changed files can't be published"} (secrets, symlinks, submodules and unsafe paths are never sent). Discard ${paths.length === 1 ? "it" : "them"} and try again.`;
  }
  return (code && sentences[code]) || message(error);
}

/** Why the last read of GitHub failed, for the "PR status unavailable" line. */
export function syncErrorMessage(code: SyncError): string {
  return sentences[code] ?? "GitHub could not be read.";
}

/** Whether the person has to sign in to GitHub again to get past this. */
export function needsReconnect(code: string | null | undefined): boolean {
  return code === "github_reauthorization_required";
}

/**
 * Whether a retry of the same action should reuse its idempotency key: only when the first attempt may have been acted on
 * (no answer, a server fault, or an unknown outcome). A definite refusal (409, 403…) is answered afresh.
 */
export function reuseKey(error: unknown): boolean {
  return !(error instanceof ApiError) || error.status >= 500 || !error.code;
}
