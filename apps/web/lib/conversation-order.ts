import type { ConversationMessage, ConversationSession } from "@/lib/workspace-api";

/** Older in-flight list/read responses cannot undo a newer admission or Stop. */
export function mergeSessions(...batches: readonly ConversationSession[][]): ConversationSession[] {
  const sessions = new Map<string, ConversationSession>();
  for (const batch of batches) for (const session of batch) {
    const current = sessions.get(session.id);
    if (!current || (session.revision ?? 0) > (current.revision ?? 0) ||
        ((session.revision ?? 0) === (current.revision ?? 0) && session.updated_at >= current.updated_at)) sessions.set(session.id, session);
  }
  return [...sessions.values()].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
}

/** Intake sequence stays stable for pagination; display groups replies by turn. */
export function conversationOrder(messages: readonly ConversationMessage[]): ConversationMessage[] {
  const order = new Map<string, number>();
  for (const message of messages) if (message.role === "user" && message.turn_id) order.set(message.turn_id, message.sequence);
  return messages.filter((message) => !["queued", "blocked"].includes(message.turn_state ?? "")).toSorted((a, b) => {
    const left = a.turn_id ? order.get(a.turn_id) ?? a.sequence : a.sequence;
    const right = b.turn_id ? order.get(b.turn_id) ?? b.sequence : b.sequence;
    return left - right || (a.role === "user" ? -1 : 1);
  });
}
