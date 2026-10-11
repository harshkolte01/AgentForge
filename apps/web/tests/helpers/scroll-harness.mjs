// A simulated scroller for the scroll controller: a column of blocks, a viewport, a composer and a clock that only moves
// when a test says so. No DOM; the rules under test are the controller's, not the browser's.
import { createScrollController } from "../../lib/scroll-controller.ts";

export function scrollHarness({ viewport = 600, composer = 100, blocks = [], options, reducedMotion = false, turn = null } = {}) {
  const state = {
    top: 0,
    viewport,
    composer,
    /** Blocks in document order: { id, h }. Their positions are derived, so inserting above shifts everything below. */
    blocks: blocks.map((block) => ({ ...block })),
    /** The id of the block that starts the latest turn (for `turnTop`). */
    turn,
    /** Blank room under the content (the reserved space) and the padding the real content ends in front of. */
    room: 0,
    reducedMotion,
    scrolls: [],
    rooms: [],
    time: 1_000_000,
    frames: [],
    timers: [],
  };
  const y = (id) => { let at = 0; for (const block of state.blocks) { if (block.id === id) return at; at += block.h; } return null; };
  const contentEnd = () => state.blocks.reduce((sum, block) => sum + block.h, 0);
  const height = () => contentEnd() + state.room + state.composer;
  const maxTop = () => Math.max(0, height() - state.viewport);
  const clamp = (value) => Math.min(maxTop(), Math.max(0, value));
  const metrics = () => ({ top: state.top, height: height(), viewport: state.viewport, composer: state.composer, below: contentEnd() - (state.top + state.viewport - state.composer) });

  const host = {
    metrics,
    scrollTo(top, behavior) { state.scrolls.push({ top, behavior }); state.top = clamp(top); },
    captureAnchor() {
      for (const block of state.blocks) {
        const at = y(block.id);
        if (at + block.h > state.top + 1) return { id: block.id, offset: at - state.top };
      }
      return null;
    },
    anchorShift(anchor) {
      const at = y(anchor.id);
      return at === null ? null : (at - state.top) - anchor.offset;
    },
    turnTop(gap) { const at = state.turn ? y(state.turn) : null; return at === null ? null : at - gap; },
    setRoom(px) { state.rooms.push(px); },
    reducedMotion: () => state.reducedMotion,
  };
  const controller = createScrollController({
    host,
    now: () => state.time,
    requestFrame(callback) { state.frames.push(callback); return state.frames.length; },
    cancelFrame(handle) { state.frames[handle - 1] = null; },
    setTimeout(callback, ms) { const timer = { at: state.time + ms, callback, live: true }; state.timers.push(timer); return timer; },
    clearTimeout(timer) { timer.live = false; },
    options,
  });
  const api = {
    state, host, controller,
    /** Content changed: grow the block, the way a streamed answer grows. */
    grow(id, by) { state.blocks.find((block) => block.id === id).h += by; },
    insertAbove(id, block) { state.blocks.splice(state.blocks.findIndex((item) => item.id === id), 0, block); },
    add(block) { state.blocks.push(block); },
    /** The reader scrolls: the position changes, then the scroller reports it. */
    userScroll(to) { state.top = clamp(to); state.time += 1; controller.scrolled(); },
    /** Time passes (the programmatic windows close, smooth scrolls land). Frames run first. */
    tick(ms = 0) {
      state.time += ms;
      for (const timer of state.timers) if (timer.live && timer.at <= state.time) { timer.live = false; timer.callback(); }
      api.frame();
    },
    frame() { const pending = state.frames.splice(0); for (const callback of pending) callback?.(); },
    /** A layout change as the ResizeObserver reports it. */
    layout() { controller.layoutChanged(); },
    /** Lets the programmatic windows pass and reports the scroll event our own move produced. */
    settle() { state.time += 2000; controller.scrolled(); },
    snapshot: () => controller.getSnapshot(),
    maxTop,
    contentEnd,
    y,
  };
  return api;
}

/** `count` blocks of `h` pixels each, ids b0, b1, ... */
export const column = (count, h = 200) => Array.from({ length: count }, (_, i) => ({ id: `b${i}`, h }));
