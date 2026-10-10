import { SessionBoundary } from "@/components/session-boundary";
import { WorkspaceShell } from "@/components/workspace-shell";
import { WorkspaceSettings } from "@/features/sessions/workspace-settings";
export default function SettingsPage() {
  return <WorkspaceShell back><SessionBoundary><WorkspaceSettings /></SessionBoundary></WorkspaceShell>;
}
