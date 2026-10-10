import { notFound } from "next/navigation";

import { TaskDetail } from "@/components/task-detail";
import { uuidPattern } from "@/lib/routes";

export default async function ConversationRunPage({ params }: { params: Promise<{ id: string; taskId: string }> }) {
  const { id, taskId } = await params;
  if (!uuidPattern.test(id) || !uuidPattern.test(taskId)) notFound();
  return <TaskDetail key={taskId} taskId={taskId.toLowerCase()} />;
}
