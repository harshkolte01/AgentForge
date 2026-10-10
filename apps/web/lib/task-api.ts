/** Public HTTP shapes. Identity and effective authorization stay on the server. */
export type SessionInfo = {
  enabled: boolean;
  authenticated: boolean;
  mode: string;
  user?: { id: string; display_name: string };
};
export type Project = { id: string; name: string; created_at: string };
export type CodingSpec = {
  repository: { repo_id: string; ref: string };
  issue_number: number | null;
  test_profiles: string[];
  command_profiles: string[];
  max_repair_attempts: number;
};
export type CodingDetail = {
  session: { repo_id: string; commit_sha: string; revision: string; baseline_revision: string } | null;
  evidence: Array<{ id: string; kind: string; revision: string } & Record<string, unknown>>;
};
export type Task = {
  kind: "research" | "coding" | "general";
  coding: CodingSpec | null;
  id: string;
  project_id: string;
  goal: string;
  status: string;
  max_cost_usd: string;
  max_wall_time_sec: number;
  created_at: string;
  updated_at: string;
  cancel_requested_at: string | null;
  final_summary: string | null;
  final_result: Record<string, unknown> | null;
  error_code: string | null;
  event_sequence: number;
};
export type TaskStep = {
  id: string;
  step_key: string;
  agent_type: string;
  definition_version: number;
  objective: string;
  acceptance_criteria: string[];
  depends_on: string[];
  status: string;
  agent_run_id: string | null;
};
export type TaskEvent = {
  task_id: string;
  sequence: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
};
export type AgentRun = {
  agent_type?: string;
  definition_version?: number;
  parent_task_id?: string;
  budget?: Record<string, unknown>;
  effective_tool_names?: string[];
  id: string;
  status: string;
  definition_sha256?: string;
  definition_version_id?: string;
  effective_permissions_json?: Record<string, unknown>;
  effective_budget_json?: Record<string, unknown>;
  reserved_cost_usd?: string;
  measured_cost_usd?: string | null;
  error_code?: string | null;
  started_at?: string;
  finished_at?: string | null;
};
export type Source = {
  id: string;
  url: string;
  title: string;
  excerpt: string;
  source_type: "search" | "webpage" | "repository";
  fetch_status: "SEARCH_ONLY" | "FETCHED" | "FAILED";
  retrieved_at: string;
  error_code: string | null;
};
export type Artifact = {
  id: string;
  kind: string;
  filename: string;
  mime_type: string;
  byte_size: number;
  sha256: string;
  created_at: string;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const friendlyErrors: Record<string, string> = {
  session_required: "Your session has expired. Sign in again to continue.",
  session_identity_unavailable: "The configured local identity is unavailable. Check local setup.",
  invalid_bootstrap_token: "The bootstrap token was not accepted. Use the token in your private API configuration.",
  local_auth_not_configured: "Local access has not been configured. Follow the local setup instructions.",
  local_identity_not_provisioned: "Provision the configured local identity before unlocking the workspace.",
  coding_sandbox_not_configured: "Coding requires E2B_API_KEY in the API and worker configuration. Restart both after setup.",
  github_public_repository_required: "Choose a public GitHub repository. Private repository access is not available yet.",
  github_public_repository_or_ref_not_found: "The public GitHub repository or ref could not be found. Check owner/name and branch or commit.",
  github_rate_limited: "GitHub public API rate limit reached. Wait before trying again.",
  github_read_unavailable: "GitHub is unavailable. Try again later.",
  github_invalid_repository: "Enter a GitHub repository as owner/name.",
  github_issue_not_found: "The selected issue was not found in this public repository. Pull requests cannot be selected as issues.",
  github_repository_not_authorized: "Connect this repository through your GitHub App installation, then refresh repository access.",
  github_write_permission_required: "The GitHub installation needs contents and pull request write access, and publishing must be enabled by the operator.",
  github_reauthorization_required: "Your GitHub authorization needs refreshing. Sign out and continue with GitHub again.",
  github_access_denied: "GitHub denied repository access. Review the app installation and account permissions.",
  github_issues_permission_required: "The GitHub app needs issue read access to select this issue.",
  verified_coding_result_required: "This task has no publishable verified code result. Review its tests and execution evidence.",
  approval_expired: "This approval has expired. Prepare and review a new exact change.",
  approval_not_pending: "This approval has already been decided. Refresh to see its current state.",
  session_turn_in_progress: "This conversation already has an active turn. Wait for it to finish or stop it before sending another.",
  session_archived: "Restore this conversation from archived history before sending a message.",
  active_session_cannot_archive: "Wait for the active turn to finish before archiving this conversation.",
  turn_idempotency_conflict: "This retry does not match the original turn. Reload the conversation before starting a new request.",
  session_message_limit_reached: "This conversation reached its message limit. Start a new conversation to continue.",
  task_cost_limit: "This task exceeds your per-task cost limit. Lower the budget in composer settings.",
  concurrent_task_limit: "Your account already has the maximum number of active tasks. Wait for one to finish or stop it.",
  daily_task_limit: "Your account reached its daily task limit. Try again after the next UTC day begins.",
  daily_cost_limit: "Your account reached its reserved daily budget. Try again after the next UTC day begins.",
  global_concurrent_task_limit: "The service is at its active-task capacity. Try again after a task finishes.",
  global_daily_task_limit: "The service reached its daily task limit. Try again after the next UTC day begins.",
  global_daily_cost_limit: "The service reached its daily reserved budget. Try again after the next UTC day begins.",
  project_limit_reached: "This account reached its project limit. Use an existing project to continue.",
  project_agent_limit_reached: "This project reached its custom agent limit. Use an existing agent.",
  agent_version_limit_reached: "This agent reached its version limit. Create a new agent for further changes.",
  definition_version_conflict: "Another version was saved first. Refresh the agent and create its next version.",
  custom_agent_requires_explicit_restricted_resources: "Use explicit resource arrays in the agent JSON. Only allowed_web_hosts can contain public domains.",
  custom_agents_support_source_research_tools: "Custom agents support full task input reads, stored source reads, web search and source fetching with research_result_v1 output.",
  task_not_found: "This task is unavailable for the current workspace.",
  artifact_unavailable: "The report file is unavailable. Try downloading it again later.",
};

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    cache: "no-store",
    credentials: "same-origin",
    headers: { ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null;
    const detail = typeof body?.detail === "string" ? body.detail : "";
    if (response.status === 401 && path !== "/auth/session") {
      window.dispatchEvent(new Event("agentforge-session-expired"));
    }
    throw new ApiError(response.status, friendlyErrors[detail] ??
      (response.status === 422 ? "Check the goal, budget and repository or source settings, then try again." :
        `The request could not be completed (HTTP ${response.status}). Try again.`));
  }
  return response.status === 204 ? undefined as T : await response.json() as T;
}

export const terminalStatuses = new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"]);
export const eventTypes = ["task.created", "task.status", "task.cancel_requested", "task.plan.created",
  "task.step.updated", "subagent.started", "subagent.finished", "model.started", "model.finished", "tool.started", "tool.finished",
  "task.verified", "task.recovered", "task.finished", "sandbox.workspace.created", "test.finished", "task.repair.started"];
export function statusLabel(status: string): string {
  return status.toLowerCase().replaceAll("_", " ").replace(/^./, (letter) => letter.toUpperCase());
}
export function formatDate(value: string): string {
  return new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
export function message(error: unknown): string {
  return error instanceof Error ? error.message : "Could not reach the API. Check that the local server is running.";
}
export function publicSourceUrl(value: string): string | undefined {
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase();
    const publicSyntax = host.includes(".") && !/^[\d.]+$/.test(host) && !host.includes(":") &&
      !host.endsWith(".local") && !host.endsWith(".internal") && !host.endsWith(".localhost");
    return url.protocol === "https:" && publicSyntax && !url.username && !url.password &&
      (!url.port || url.port === "443") ? url.href : undefined;
  } catch {
    return undefined;
  }
}
