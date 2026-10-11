/** `installations` and `reauthorization_required` are absent from older servers: read them as unknown. */
export type GitHubStatus = { configured: boolean; connected: boolean; install_url: string | null; enabled_writes?: boolean; installations?: number | null; reauthorization_required?: boolean };
export type GitHubRepository = { id: number; full_name: string; private: boolean; default_branch: string; installation_id: number; can_write: boolean };
export function githubInstallUrl(value: string | null | undefined): string | undefined {
  try { const url = new URL(value ?? ""); return url.protocol === "https:" && url.hostname === "github.com" && !url.username && !url.password ? url.href : undefined; }
  catch { return undefined; }
}
