/** Single source of truth for browser URLs. Backend `/api/v1/*` paths live in task-api. */
export const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const routes = {
  newConversation: "/chat",
  conversation: (sessionId: string) => `/chat/${sessionId}`,
  settings: (section?: string) => section ? `/settings?section=${section}` : "/settings",
} as const;
