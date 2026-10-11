/**
 * Coalesces bursts of updates into one per animation frame: streaming text arrives in many small frames, and painting each
 * one would repaint faster than the screen refreshes. There is no artificial delay: the first update of a burst is shown on
 * the very next frame, and everything that arrived before that frame rides along.
 *
 * Animation frames do not run in a hidden tab, so a timer backs each frame up; whichever fires first flushes, and the other
 * is cancelled. A background tab therefore still hears about the end of a run.
 */
export type FrameEnv = {
  requestFrame(callback: () => void): unknown;
  cancelFrame(handle: unknown): void;
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
};

export type FrameBatcher = {
  /** Asks for `flush` to run on the next frame; a no-op when one is already due. */
  schedule(): void;
  /** Runs `flush` now if an update is waiting. */
  flushNow(): void;
  /** Drops the waiting update without running it. */
  cancel(): void;
  readonly pending: boolean;
};

export const browserFrameEnv: FrameEnv = {
  requestFrame: (callback) => requestAnimationFrame(callback),
  cancelFrame: (handle) => cancelAnimationFrame(handle as number),
  setTimeout: (callback, ms) => setTimeout(callback, ms),
  clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

/** How long a frame may be late (a hidden tab) before the timer flushes instead. */
export const FRAME_FALLBACK_MS = 250;

export function createFrameBatcher(flush: () => void, env: FrameEnv = browserFrameEnv, fallbackMs = FRAME_FALLBACK_MS): FrameBatcher {
  let frame: unknown = null;
  let timer: unknown = null;
  let waiting = false;

  function release() {
    if (frame !== null) env.cancelFrame(frame);
    if (timer !== null) env.clearTimeout(timer);
    frame = null;
    timer = null;
  }
  function run() {
    if (!waiting) return;
    waiting = false;
    release();
    flush();
  }
  return {
    schedule() {
      if (waiting) return;
      waiting = true;
      frame = env.requestFrame(run);
      timer = env.setTimeout(run, fallbackMs);
    },
    flushNow: run,
    cancel() {
      waiting = false;
      release();
    },
    get pending() { return waiting; },
  };
}
