/** Single source of truth for browser URLs. Backend `/api/v1/*` paths live in task-api. */
export const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const routes = {
  newConversation: "/chat",
  conversation: (sessionId: string) => `/chat/${sessionId}`,
  settings: (section?: string) => section ? `/settings?section=${section}` : "/settings",
} as const;

const LAST_CHAT_KEY = "agentforge.last-chat";
/** The chat URL this tab last showed, so "Back to chat" from Settings returns to that conversation. */
export function rememberChat(path: string) {
  try { sessionStorage.setItem(LAST_CHAT_KEY, path); } catch { /* Back to chat then opens a new conversation. */ }
}
export function lastChat(): string {
  try {
    const path = sessionStorage.getItem(LAST_CHAT_KEY);
    return path === routes.newConversation || (path?.startsWith("/chat/") && uuidPattern.test(path.slice(6))) ? path : routes.newConversation;
  } catch { return routes.newConversation; }
}
