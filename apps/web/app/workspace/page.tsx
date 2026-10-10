import { SessionBoundary } from "@/components/session-boundary";
import { TaskWorkspace } from "@/components/task-workspace";
import { WorkspaceShell } from "@/components/workspace-shell";

export default function WorkspacePage() {
  return <WorkspaceShell><SessionBoundary><TaskWorkspace /></SessionBoundary></WorkspaceShell>;
}
