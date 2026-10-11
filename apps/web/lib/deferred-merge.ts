/**
 * Merge is the one action here that cannot be taken back, so it waits a few seconds after the click and can be undone until then.
 * The wait lives at module level, keyed by the pull request's URL: closing the panel or moving between conversations neither
 * cancels nor repeats it. Reloading the page does cancel it (nothing was merged). A merge that fails after the wait leaves a
 * notice for whichever toaster is mounted, so a failure is never silent.
 */
export const UNDO_WINDOW_MS = 5000;

/** Whole seconds left in an undo window, never more than the window itself (a clock read a moment ago must not say 6). */
export const secondsLeft = (endsAt: number, now: number) => Math.min(UNDO_WINDOW_MS / 1000, Math.max(0, Math.ceil((endsAt - now) / 1000)));

export type PendingMerge = { key: string; label: string; endsAt: number; phase: "waiting" | "merging" };
export type MergeNotice = { id: number; tone: "success" | "error"; text: string };
export type MergeSnapshot = { pending: readonly PendingMerge[]; notices: readonly MergeNotice[] };

type Clock = { now: () => number; setTimeout: (run: () => void, ms: number) => unknown; clearTimeout: (handle: unknown) => void };
const realClock: Clock = { now: () => Date.now(), setTimeout: (run, ms) => setTimeout(run, ms), clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>) };

export type StartOptions = {
  label: string;
  /** Does the merge. Called once, after the wait, unless it was undone. */
  run: () => Promise<unknown>;
  /** The sentence for a notice when `run` fails. */
  failure: (error: unknown) => string;
  /** The sentence for a notice when `run` succeeds, if any. */
  success?: string;
};

export function createDeferredMerge(clock: Clock = realClock, delay = UNDO_WINDOW_MS) {
  const timers = new Map<string, unknown>();
  const listeners = new Set<() => void>();
  let pending: PendingMerge[] = [];
  let notices: MergeNotice[] = [];
  let snapshot: MergeSnapshot = { pending, notices };
  let nextNotice = 1;
  const publish = () => { snapshot = { pending, notices }; listeners.forEach((listener) => listener()); };
  const settle = (key: string) => { pending = pending.filter((item) => item.key !== key); timers.delete(key); };
  const notify = (tone: MergeNotice["tone"], text: string) => { notices = [...notices.slice(-4), { id: nextNotice++, tone, text }]; };

  return {
    /** Starts the wait. False when this pull request already has a merge waiting or under way. */
    start(key: string, options: StartOptions): boolean {
      if (pending.some((item) => item.key === key)) return false;
      pending = [...pending, { key, label: options.label, endsAt: clock.now() + delay, phase: "waiting" }];
      timers.set(key, clock.setTimeout(() => {
        pending = pending.map((item) => (item.key === key ? { ...item, phase: "merging" } : item));
        publish();
        let attempt: Promise<unknown>;
        try { attempt = options.run(); } catch (error) { attempt = Promise.reject(error); }
        attempt.then(
          () => { settle(key); if (options.success) notify("success", options.success); publish(); },
          (error: unknown) => { settle(key); notify("error", options.failure(error)); publish(); },
        );
      }, delay));
      publish();
      return true;
    },
    /** Undo: only while waiting. False once the merge is under way, when it can no longer be stopped. */
    cancel(key: string): boolean {
      const item = pending.find((entry) => entry.key === key);
      if (!item || item.phase !== "waiting") return false;
      clock.clearTimeout(timers.get(key));
      settle(key);
      publish();
      return true;
    },
    get: (key: string) => pending.find((item) => item.key === key),
    dismiss(id: number) { notices = notices.filter((item) => item.id !== id); publish(); },
    subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    getSnapshot: () => snapshot,
  };
}

export const deferredMerge = createDeferredMerge();
