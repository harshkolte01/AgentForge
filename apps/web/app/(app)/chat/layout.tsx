import { ConversationWorkspace } from "@/features/sessions/conversation-workspace";

/** The workspace shell (sidebar, history, composer) lives in this layout so it stays mounted while people move between conversations. */
export default function ChatLayout() {
  return <ConversationWorkspace />;
}
