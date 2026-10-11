/**
 * Loads a finished reply's activity: every event of its task, page after page, folded through the same reducer the live turn
 * uses, so history and a live run cannot disagree. No page-count cutoff: a long run is read to its end.
 */
import { createActivityState, reduceActivity } from "@/lib/activity/reducer";
import { loadActivityHistory } from "@/lib/activity/sync";
import type { ActivityEntry, MessageActivity, TaskEvent } from "@/lib/activity/types";
import { endEntries } from "@/features/sessions/timeline-model";
import { EVENT_PAGE_LIMIT } from "@/lib/task-api";

export type HistoryFetcher = (after: number, limit: number, signal?: AbortSignal) => Promise<readonly TaskEvent[]>;

/**
 * The entries of a task whose run is over. When the message's activity says the task is terminal, calls that never reported
 * back come out as `unknown` instead of running. Rejects (and never returns partial entries) when a page cannot be read, so
 * the caller can offer a retry instead of showing a silently shortened list.
 */
export async function loadHistoryEntries(
  taskId: string,
  activity: Pick<MessageActivity, "terminal"> | null | undefined,
  fetchHistory: HistoryFetcher,
  signal?: AbortSignal,
): Promise<readonly ActivityEntry[]> {
  const events = await loadActivityHistory(fetchHistory, { signal, pageSize: EVENT_PAGE_LIMIT });
  const state = reduceActivity(createActivityState(taskId), { type: "events", events });
  // Everything the server has was read, so nothing more can report back: older tasks have no task.finished event to say the
  // run is over, and a log with a hole would otherwise leave a call spinning. The message's own terminal flag decides.
  return activity?.terminal ? endEntries(state.view) : state.view;
}
