import { SessionBoundary } from "@/components/session-boundary";
import { ConversationWorkspace } from "@/features/sessions/conversation-workspace";
export default function AgentsPage() {
  return <SessionBoundary embedded><ConversationWorkspace initialSurface="agents" createAgent /></SessionBoundary>;
}
