import { SessionBoundary } from "@/components/session-boundary";
import { ConversationWorkspace } from "@/features/sessions/conversation-workspace";
export default function WorkspacePage() {
  return <SessionBoundary embedded><ConversationWorkspace /></SessionBoundary>;
}
