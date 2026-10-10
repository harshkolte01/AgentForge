import { SessionBoundary } from "@/components/session-boundary";
import { GitHubConnection } from "@/features/github/github-connection";
import { WorkspaceShell } from "@/components/workspace-shell";
export default function ConnectionsPage() {
  return <WorkspaceShell back><SessionBoundary><GitHubConnection /></SessionBoundary></WorkspaceShell>;
}
