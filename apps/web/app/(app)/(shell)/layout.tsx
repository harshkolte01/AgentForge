import { ConversationWorkspace } from "@/features/sessions/conversation-workspace";

/**
 * The workspace shell (sidebar, history, composer) lives in this layout so it stays mounted while people move between
 * conversations and the Plugins page. `/chat` and `/chat/<id>` pages render nothing of their own (the shell draws the thread);
 * `/plugins` hands the shell its page.
 */
export default function ShellLayout({ children }: { children: React.ReactNode }) {
  return <ConversationWorkspace>{children}</ConversationWorkspace>;
}
