/**
 * The conversation's one scroll controller: following the live edge, anchoring a new turn near the top, keeping the room
 * reserved beneath it, the "Jump to latest" button, and keeping the reader's place while content changes around them.
 *
 * It is a plain state machine. Everything that touches the page (measuring, scrolling, finding the first visible element,
 * frames, timers) is injected, so the rules below run under test without a browser; `features/sessions/use-conversation-scroll.ts`
 * is the thin adapter that wires them to the DOM.
 *
 * Rules:
 *  - The controller FOLLOWS the live edge while the reader is at the bottom: whenever the layout changes it scrolls so the end
 *    of the real content (not the blank room reserved under the latest turn) stays just above the composer.
 *  - Following PAUSES the moment the reader acts: wheel, touch drag, navigation keys, grabbing the scrollbar, selecting text,
 *    toggling a disclosure, or loading earlier messages. An upward scroll nobody asked for (focus scrolling into view) pauses too.
 *    An input that can only move toward the live edge, while already at it, pauses nothing.
 *  - Following RESUMES only by the explicit jump, a new turn, or a deliberate return: a user scroll DOWNWARD that ends within
 *    `edgePx` of the live edge (while no scrollbar drag or text selection is still in progress). Programmatic scrolls never
 *    count as the reader scrolling: scroll events inside the window a programmatic scroll opens are not the reader's.
 *  - While not following, the reader's place is kept: the first visible element and its offset are remembered, and after any
 *    layout change (history prepended, a disclosure above the viewport opening, the composer resizing, a live row replaced by
 *    the saved one) the scroll position is shifted by exactly how far that element moved.
 */

export type PauseReason = "wheel" | "touch" | "keyboard" | "scrollbar" | "selection" | "toggle" | "history" | "scroll";

export type ScrollMetrics = {
  /** The scroller's `scrollTop`. */
  top: number;
  /** Its `scrollHeight`. */
  height: number;
  /** Its visible height. */
  viewport: number;
  /** Pixels of real content below the visible area (the composer covers the bottom). Zero or negative when the end is in view. */
  below: number;
  /** Height of the composer that covers the bottom of the scroller. */
  composer: number;
};

/** Whatever the host needs to find the reader's place again. Opaque to the controller. */
export type Anchor = unknown;

export type ScrollHost = {
  metrics(): ScrollMetrics;
  scrollTo(top: number, behavior: "auto" | "smooth"): void;
  /** The first visible element and where it is, or null when nothing can serve as an anchor. */
  captureAnchor(): Anchor | null;
  /** How far the anchor moved down (positive) or up (negative) since it was captured; null when it cannot be found any more. */
  anchorShift(anchor: Anchor): number | null;
  /** The scroll position that puts the latest turn `gap` pixels below the top of the view, or null when there is no turn. */
  turnTop(gap: number): number | null;
  /** Publishes the blank room reserved beneath the latest turn. */
  setRoom(px: number): void;
  reducedMotion(): boolean;
};

export type ScrollOptions = {
  /** Within this distance of the live edge counts as "at the edge". */
  edgePx: number;
  /** The jump button shows when more than this much real content is below the view. */
  jumpPx: number;
  /** Following leaves this much clear space between the end of the content and the composer. */
  liveMargin: number;
  /** Gap left above a turn that is anchored to the top. */
  turnGap: number;
  /** Blank space kept above and below the latest turn so it can be scrolled to the top. */
  turnClearance: number;
  /** How long scroll events after an instant programmatic scroll are not the reader's. */
  programmaticMs: number;
  /** The same, for a smooth programmatic scroll. */
  programmaticSmoothMs: number;
  /** How long a disclosure toggle or history load keeps its own place pinned. */
  pinMs: number;
};

export const defaultScrollOptions: ScrollOptions = {
  edgePx: 24, jumpPx: 120, liveMargin: 16, turnGap: 20, turnClearance: 40, programmaticMs: 120, programmaticSmoothMs: 900, pinMs: 2000,
};

export type ScrollSnapshot = {
  /** The view tracks the live edge. */
  following: boolean;
  /** Real content lies below the view and the reader is not following: show "Jump to latest". */
  jumpVisible: boolean;
  /** What paused following, for diagnostics and browser checks. */
  pausedBy: PauseReason | null;
};

export type ScrollDeps = {
  host: ScrollHost;
  now(): number;
  requestFrame(callback: () => void): unknown;
  cancelFrame(handle: unknown): void;
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
  options?: Partial<ScrollOptions>;
};

export type ScrollController = {
  getSnapshot(): ScrollSnapshot;
  subscribe(listener: () => void): () => void;
  /** The reader moved the view by wheel, touch drag or key. `deltaY` > 0 means toward newer content. */
  input(reason: "wheel" | "touch" | "keyboard", deltaY: number): void;
  scrollbarDown(): void;
  /** A pointer was released anywhere: ends a scrollbar drag. */
  pointerUp(): void;
  /** Whether the thread holds a non-collapsed text selection. Call when it changes. */
  selection(active: boolean): void;
  /** A disclosure toggle is about to change heights: keep its own place (`anchor` is the toggle's). */
  toggle(anchor: Anchor | null): void;
  /** Earlier messages are about to be inserted above: keep the reader's place. */
  holdPlace(): void;
  /** The scroller reported a scroll. */
  scrolled(): void;
  /** The layout changed (call from a ResizeObserver callback: it is already frame-aligned). */
  layoutChanged(): void;
  /** The layout may have changed; handle it on the next frame (coalesced). */
  requestPass(): void;
  /**
   * Content above what the reader is looking at just changed height by `px` (negative: it shrank), already laid out: scroll by
   * the same amount so the view does not move, whether following or not. Used when a finished run folds its steps away.
   */
  shift(px: number): void;
  /** The reader sent a message: pin its turn near the top and follow what is written beneath it. */
  beginTurn(): void;
  /** A conversation was opened: land on its latest content, or on the running turn's start. */
  landOnLatest(options: { turn: boolean }): void;
  /** The "Jump to latest" button. */
  jump(): void;
  /** Drops pending frames and timers (the scroller went away). The controller can be used again afterwards. */
  cancel(): void;
};

const sameSnapshot = (a: ScrollSnapshot, b: ScrollSnapshot) => a.following === b.following && a.jumpVisible === b.jumpVisible && a.pausedBy === b.pausedBy;

/** Which way a key scrolls the page, when it does: "down" is toward newer content. */
export function keyScrollDirection(key: string, shiftKey: boolean, target: { editable: boolean; activates: boolean }): "up" | "down" | null {
  if (target.editable) return null;
  switch (key) {
    case "PageUp": case "ArrowUp": case "Home": return "up";
    case "PageDown": case "ArrowDown": case "End": return "down";
    // Space activates a button or link instead of scrolling; Shift+Space scrolls the other way.
    case " ": case "Spacebar": return target.activates ? null : shiftKey ? "up" : "down";
    default: return null;
  }
}

/** Whether a pointer press landed on the scroller's own scrollbar (its gutter), not on content or a child. */
export function isScrollbarPointerDown(input: { targetIsScroller: boolean; pointerType: string; clientX: number; right: number; scrollbarWidth: number }): boolean {
  if (!input.targetIsScroller || input.pointerType === "touch") return false;
  // Overlay scrollbars take no layout width but can still be grabbed along the edge.
  const gutter = input.scrollbarWidth > 0 ? input.scrollbarWidth : 14;
  return input.clientX >= input.right - gutter;
}

/** The blank room that lets the latest turn sit at the top of the view with a gap, with the composer and clearance accounted for. */
export function reservedRoom(viewport: number, composer: number, clearance: number): number {
  return Math.max(0, viewport - composer - clearance);
}

/** Where to scroll so the end of the real content sits `margin` above the composer. */
export function liveEdgeTop(metrics: Pick<ScrollMetrics, "top" | "below">, margin: number): number {
  return metrics.top + metrics.below + margin;
}

export function createScrollController(deps: ScrollDeps): ScrollController {
  const { host } = deps;
  const options: ScrollOptions = { ...defaultScrollOptions, ...deps.options };
  const listeners = new Set<() => void>();
  let following = true;
  let pausedBy: PauseReason | null = null;
  let heldScrollbar = false;
  let heldSelection = false;
  let anchor: Anchor | null = null;
  let pinned: { anchor: Anchor | null; until: number } | null = null;
  let programmaticUntil = 0;
  // A smooth scroll in flight owns the view until it lands; layout passes in between must not cut it short.
  let smoothUntil = 0;
  let smoothTimer: unknown = null;
  let lastTop = 0;
  let lastHeight = 0;
  let turnPin: { behavior: "auto" | "smooth" } | null = null;
  let room = -1;
  let frame: unknown = null;
  let snapshot: ScrollSnapshot = { following: true, jumpVisible: false, pausedBy: null };

  const held = () => heldScrollbar || heldSelection;
  const atEdge = (metrics: ScrollMetrics) => metrics.below <= options.edgePx;

  function publish(metrics: ScrollMetrics) {
    const next: ScrollSnapshot = { following, pausedBy, jumpVisible: !following && metrics.below > options.jumpPx };
    if (sameSnapshot(snapshot, next)) return;
    snapshot = next;
    listeners.forEach((listener) => listener());
  }
  function move(top: number, behavior: "auto" | "smooth") {
    const smooth = behavior === "smooth" && !host.reducedMotion();
    const now = deps.now();
    programmaticUntil = now + (smooth ? options.programmaticSmoothMs : options.programmaticMs);
    if (smooth) {
      smoothUntil = programmaticUntil;
      if (smoothTimer !== null) deps.clearTimeout(smoothTimer);
      // Once it has landed, catch up with whatever grew meanwhile.
      smoothTimer = deps.setTimeout(() => { smoothTimer = null; requestPass(); }, options.programmaticSmoothMs);
    } else smoothUntil = 0;
    host.scrollTo(Math.max(0, top), smooth ? "smooth" : "auto");
  }
  function pause(reason: PauseReason, metrics: ScrollMetrics) {
    turnPin = null;
    pinned = null;
    if (following) anchor = host.captureAnchor();
    following = false;
    pausedBy = reason;
    publish(metrics);
  }
  function resume(metrics: ScrollMetrics) {
    following = true;
    pausedBy = null;
    anchor = null;
    pinned = null;
    publish(metrics);
  }
  /** The reader is at the live edge and nothing is still being dragged or selected: following carries on. */
  function resumeAtEdge(metrics: ScrollMetrics) {
    if (!following && !held() && atEdge(metrics)) resume(metrics);
  }

  function pass() {
    const metrics = host.metrics();
    const nextRoom = reservedRoom(metrics.viewport, metrics.composer, options.turnClearance);
    if (nextRoom !== room) { room = nextRoom; host.setRoom(nextRoom); }
    if (turnPin) {
      const { behavior } = turnPin;
      turnPin = null;
      const top = host.turnTop(options.turnGap);
      if (top !== null) move(top, behavior);
    } else if (following) {
      if (metrics.below > 0 && deps.now() >= smoothUntil) move(liveEdgeTop(metrics, options.liveMargin), "auto");
    } else {
      const place = pinned && deps.now() < pinned.until ? pinned.anchor : anchor;
      const shift = place === null ? null : host.anchorShift(place);
      if (shift !== null && Math.abs(shift) >= 1) move(metrics.top + shift, "auto");
    }
    lastHeight = metrics.height;
    // Positions have settled: remember where the reader is now. Not needed while following the live edge.
    const settled = host.metrics();
    if (!following) {
      anchor = host.captureAnchor();
      // A pause that was not the reader moving the view (a toggle, earlier messages) ends where the view is at the edge again.
      if ((pausedBy === "toggle" || pausedBy === "history") && !held() && atEdge(settled)) { resume(settled); return; }
    }
    publish(settled);
  }

  function requestPass() {
    if (frame !== null) return;
    frame = deps.requestFrame(() => { frame = null; pass(); });
  }

  return {
    getSnapshot: () => snapshot,
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    input(reason, deltaY) {
      if (deltaY === 0) return;
      const metrics = host.metrics();
      // Moving toward the live edge while already there changes nothing; moving up with nothing above is not a scroll.
      if (deltaY > 0 && atEdge(metrics)) { resumeAtEdge(metrics); return; }
      if (deltaY < 0 && metrics.top <= 0) return;
      pause(reason, metrics);
    },
    scrollbarDown() {
      heldScrollbar = true;
      pause("scrollbar", host.metrics());
    },
    pointerUp() {
      if (!heldScrollbar) return;
      heldScrollbar = false;
      resumeAtEdge(host.metrics());
    },
    selection(active) {
      if (heldSelection === active) return;
      heldSelection = active;
      if (active) pause("selection", host.metrics());
      else resumeAtEdge(host.metrics());
    },
    toggle(place) {
      const metrics = host.metrics();
      pause("toggle", metrics);
      // The toggle itself is the place to keep, not whatever happens to be first in view.
      pinned = { anchor: place ?? anchor, until: deps.now() + options.pinMs };
    },
    holdPlace() {
      const metrics = host.metrics();
      pause("history", metrics);
      pinned = { anchor: host.captureAnchor(), until: deps.now() + options.pinMs };
    },
    scrolled() {
      const metrics = host.metrics();
      const reader = deps.now() >= programmaticUntil;
      const movedDown = metrics.top > lastTop + 0.5;
      const movedUp = metrics.top < lastTop - 0.5;
      const shrank = metrics.height < lastHeight;
      lastTop = metrics.top;
      lastHeight = metrics.height;
      if (reader) {
        pinned = null;
        // A scroll nobody here caused, away from the edge. A shrinking page clamps the position by itself; a bounce at the end returns to the edge.
        if (following && movedUp && !shrank && !atEdge(metrics)) { pause("scroll", metrics); return; }
        if (!following && movedDown) resumeAtEdge(metrics);
      }
      // Even a programmatic scroll updates the remembered place: a reader's own scroll inside its window must not be undone.
      if (!following) anchor = host.captureAnchor();
      publish(metrics);
    },
    layoutChanged() {
      if (frame !== null) { deps.cancelFrame(frame); frame = null; }
      pass();
    },
    requestPass,
    shift(px) {
      if (Math.abs(px) < 1) return;
      move(host.metrics().top + px, "auto");
      // The view is where the reader left it: remember that place, so the next pass does not "correct" it back.
      pinned = null;
      if (!following) anchor = host.captureAnchor();
      requestPass();
    },
    beginTurn() {
      resume(host.metrics());
      turnPin = { behavior: "smooth" };
      requestPass();
    },
    landOnLatest({ turn }) {
      resume(host.metrics());
      if (turn) turnPin = { behavior: "auto" };
      requestPass();
    },
    jump() {
      const metrics = host.metrics();
      turnPin = null;
      resume(metrics);
      if (metrics.below > 0) move(liveEdgeTop(metrics, options.liveMargin), "smooth");
    },
    cancel() {
      if (frame !== null) deps.cancelFrame(frame);
      if (smoothTimer !== null) deps.clearTimeout(smoothTimer);
      frame = null; smoothTimer = null;
      turnPin = null;
    },
  };
}
