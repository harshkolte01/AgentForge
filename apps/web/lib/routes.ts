/** Single source of truth for browser URLs. Backend `/api/v1/*` paths live in task-api. */
export const uuidPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const routes = {
  newConversation: "/chat",
  conversation: (sessionId: string) => `/chat/${sessionId}`,
  /** Run details open inside their conversation so Back returns to the same thread. */
  run: (sessionId: string, taskId: string) => `/chat/${sessionId}/runs/${taskId}`,
  /** Standalone run page for tasks that do not belong to a conversation (for example agent tests). */
  standaloneRun: (taskId: string) => `/tasks/${taskId}`,
  settings: (section?: string) => section ? `/settings?section=${section}` : "/settings",
} as const;
