import type { ReactNode } from "react";

import { SessionBoundary } from "@/components/session-boundary";
import { ConversationWorkspace } from "@/features/sessions/conversation-workspace";

/**
 * The workspace shell (sidebar, history, composer) lives in this layout so it stays mounted while
 * people move between conversations and run details; only the URL and the selected thread change.
 */
export default function ChatLayout({ children }: { children: ReactNode }) {
  return <SessionBoundary embedded><ConversationWorkspace>{children}</ConversationWorkspace></SessionBoundary>;
}
