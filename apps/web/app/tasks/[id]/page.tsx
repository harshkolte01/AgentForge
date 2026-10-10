import { notFound } from "next/navigation";

import { SessionBoundary } from "@/components/session-boundary";
import { TaskDetail } from "@/components/task-detail";
import { WorkspaceShell } from "@/components/workspace-shell";

export default async function TaskPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)) notFound();
  const taskId = id.toLowerCase();
  return <WorkspaceShell back><SessionBoundary><TaskDetail key={taskId} taskId={taskId} /></SessionBoundary></WorkspaceShell>;
}
