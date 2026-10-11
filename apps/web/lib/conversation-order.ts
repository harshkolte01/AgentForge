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

/**
 * Whether a fresh read of a conversation shows nothing new: same revision and update time, and the same fields the sidebar,
 * header and composer read. Keeping the old object then lets React skip re-rendering the whole workspace on every poll.
 */
export function sameSession(a: ConversationSession, b: ConversationSession): boolean {
  return a.id === b.id && a.revision === b.revision && a.updated_at === b.updated_at && a.status === b.status && a.last_sequence === b.last_sequence &&
    a.read_sequence === b.read_sequence && a.active_task_id === b.active_task_id && a.queued_count === b.queued_count && a.queue_paused === b.queue_paused &&
    a.archived === b.archived && a.title === b.title && a.repository?.repo_id === b.repository?.repo_id && a.repository?.ref === b.repository?.ref;
}

/** Puts a fresh read of one conversation into the list, newest first; the same list when nothing changed or the read is older. */
export function upsertSession(current: ConversationSession[], session: ConversationSession): ConversationSession[] {
  const previous = current.find((item) => item.id === session.id);
  if (previous && ((previous.revision ?? 0) > (session.revision ?? 0) || sameSession(previous, session))) return current;
  return [session, ...current.filter((item) => item.id !== session.id)].sort((a, b) => b.updated_at.localeCompare(a.updated_at));
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
