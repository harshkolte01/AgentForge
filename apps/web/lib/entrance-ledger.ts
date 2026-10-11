/**
 * Which rows of the thread play their entrance animation (`message-in`): once, for content that genuinely arrived while the
 * reader was here. History, a refresh, a row that moves between lists, a remount and the live-to-saved handoff never replay it.
 *
 * Ids are React keys of rows, so a row keeps its entrance state when the same content is re-keyed to the same identity (the
 * pending bubble and the message the server saved for it share one id). A row asks `shouldAnimate` once when it mounts and
 * reports `played` once it is on screen.
 */
export type EntranceLedger = {
  /** Rows that were already there (the first load, earlier history): they never animate. */
  seed(ids: Iterable<string>): void;
  /** Rows that arrived while the reader was here: each animates once. */
  arrived(ids: Iterable<string>): void;
  /** A task's reply already appeared live, so the message saved for it takes over in place, without a second entrance. */
  streamed(taskId: string): void;
  shouldAnimate(id: string, taskId?: string | null): boolean;
  played(id: string): void;
};

export function createEntranceLedger(): EntranceLedger {
  const eligible = new Set<string>();
  const done = new Set<string>();
  const streamedTasks = new Set<string>();
  return {
    seed(ids) { for (const id of ids) { eligible.delete(id); done.add(id); } },
    arrived(ids) { for (const id of ids) if (!done.has(id)) eligible.add(id); },
    streamed(taskId) { streamedTasks.add(taskId); },
    shouldAnimate(id, taskId) { return eligible.has(id) && !done.has(id) && !(taskId && streamedTasks.has(taskId)); },
    played(id) { eligible.delete(id); done.add(id); },
  };
}
