export type GitHubStatus = { configured: boolean; connected: boolean; install_url: string | null };
export type GitHubRepository = { id: number; full_name: string; private: boolean; default_branch: string; installation_id: number; can_write: boolean };
export type GitHubIssue = { number: number; title: string; state: string; url: string };
export type Approval = {
  id: string; task_id: string; state: string; action_hash: string; expires_at: string;
  action: { repo_id: string; repository_id: number; base_branch: string; base_sha: string; head_branch: string; diff_artifact_id: string; diff_sha256: string; revision: string; title: string; body: string; requested_operations: string[] };
  result?: Record<string, unknown> | null;
};
export function githubInstallUrl(value: string | null | undefined): string | undefined {
  try { const url = new URL(value ?? ""); return url.protocol === "https:" && url.hostname === "github.com" && !url.username && !url.password ? url.href : undefined; }
  catch { return undefined; }
}
