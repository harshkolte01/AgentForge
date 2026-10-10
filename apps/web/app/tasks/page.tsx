import { SessionBoundary } from "@/components/session-boundary";
import { TaskWorkspace } from "@/components/task-workspace";
import { WorkspaceShell } from "@/components/workspace-shell";
export default function TasksPage() {
  return <WorkspaceShell back><SessionBoundary><TaskWorkspace /></SessionBoundary></WorkspaceShell>;
}
