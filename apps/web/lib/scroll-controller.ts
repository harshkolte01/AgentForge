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
 *  - A sent message's turn is PINNED near the top (a gap above it) and the reply is written beneath it; the view follows only once
 *    that content outgrows the room. The pin is a state, not a one-off scroll: it is kept until the view is actually there, so a
 *    scroll cut short by anything (a focus change, a layout shift) is finished instead of being replaced by following the live edge.
 *    The reader's own input ends it.
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
  /** How long a new turn keeps trying to reach the top of the view before the controller gives up and follows the live edge. */
  turnPinMs: number;
  /** A smooth scroll of ours has stopped (landed, or was cut short) once its scroll events are this quiet. */
  smoothQuietMs: number;
};

export const defaultScrollOptions: ScrollOptions = {
  edgePx: 24, jumpPx: 120, liveMargin: 16, turnGap: 20, turnClearance: 40, programmaticMs: 120, programmaticSmoothMs: 900, pinMs: 2000, turnPinMs: 3000, smoothQuietMs: 160,
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
  /** The scroller went away: drops pending frames and timers and forgets its view. The controller can be bound to another scroller afterwards. */
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
  let quietTimer: unknown = null;
  let lastTop = 0;
  let lastHeight = 0;
  // A turn that is being brought to the top. It stays until the view is there: a smooth scroll can be cut short by anything
  // (a focus change, a layout shift, the browser), and a turn left half-way would otherwise be dragged to the live edge instead.
  let turnPin: { behavior: "auto" | "smooth"; since: number } | null = null;
  let room = -1;
  // The pinned turn reached the top and its content still fits in the room under it: it is kept there (a composer that
  // shrinks, a view that grows, or text above that re-wraps would otherwise slide it down), until the reply outgrows the room
  // or the reader moves the view.
  let resting = false;
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
  /**
   * While a smooth scroll of ours is in flight: when its scroll events go quiet it has stopped, wherever it stopped. The window
   * it opened ends there, so what grew meanwhile is caught up with, or a pin that was cut short is finished, without waiting out
   * the window's full length.
   */
  function watchSmooth(ms: number) {
    if (quietTimer !== null) deps.clearTimeout(quietTimer);
    quietTimer = deps.setTimeout(() => { quietTimer = null; if (deps.now() < smoothUntil) { smoothUntil = 0; requestPass(); } }, ms);
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
      // The first scroll event may take a moment to arrive; after that each one pushes the check back.
      watchSmooth(options.smoothQuietMs * 2);
    } else smoothUntil = 0;
    host.scrollTo(Math.max(0, top), smooth ? "smooth" : "auto");
  }
  function pause(reason: PauseReason, metrics: ScrollMetrics) {
    turnPin = null;
    resting = false;
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

  /** Brings the pinned turn to the top, again if something moved the view off it, and lets go once it is there. */
  function settleTurnPin(metrics: ScrollMetrics, roomChanged: boolean) {
    if (!turnPin) return;
    const target = host.turnTop(options.turnGap);
    // No turn to pin, or it never got there: following the live edge is better than waiting for ever.
    if (target === null || deps.now() - turnPin.since > options.turnPinMs) { turnPin = null; resting = false; return; }
    if (roomChanged) { requestPass(); return; }
    // The most the page can scroll is the end of its room: a short page cannot put the turn at the top, and is as close as it gets.
    const reachable = Math.min(Math.max(0, target), Math.max(0, metrics.height - metrics.viewport));
    if (Math.abs(metrics.top - reachable) <= 1.5) { turnPin = null; resting = true; return; }
    const { behavior } = turnPin;
    // Only the first attempt is animated; if it was cut short, the retry lands at once.
    turnPin = { behavior: "auto", since: turnPin.since };
    move(target, behavior);
    requestPass();
  }

  function pass() {
    const metrics = host.metrics();
    const nextRoom = reservedRoom(metrics.viewport, metrics.composer, options.turnClearance);
    const roomChanged = nextRoom !== room;
    if (roomChanged) { room = nextRoom; host.setRoom(nextRoom); }
    if (turnPin) {
      // A smooth scroll in flight owns the view until it lands. A new room is written on the next frame, and a turn cannot be
      // scrolled to the top before the room under it exists, so look again then.
      if (deps.now() >= smoothUntil) settleTurnPin(metrics, roomChanged);
    } else if (following) {
      if (resting && metrics.below > 0) resting = false; // the reply outgrew the room: follow it from here
      if (resting) {
        const target = host.turnTop(options.turnGap);
        if (target === null) resting = false;
        else if (roomChanged) requestPass(); // the new room is written next frame: put the turn back after that
        else if (deps.now() >= smoothUntil) {
          const reachable = Math.min(Math.max(0, target), Math.max(0, metrics.height - metrics.viewport));
          if (Math.abs(metrics.top - reachable) > 1.5) move(target, "auto");
        }
      } else if (metrics.below > 0 && deps.now() >= smoothUntil) move(liveEdgeTop(metrics, options.liveMargin), "auto");
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
      resting = false;
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
      if (deps.now() < smoothUntil) watchSmooth(options.smoothQuietMs);
      if (reader) {
        pinned = null;
        // The reader moved the view themselves (not the browser clamping a page that just got shorter).
        if (!shrank && (movedUp || movedDown)) resting = false;
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
      resting = false; // the view now keeps the answer still, not the turn's top
      move(host.metrics().top + px, "auto");
      // The view is where the reader left it: remember that place, so the next pass does not "correct" it back.
      pinned = null;
      if (!following) anchor = host.captureAnchor();
      requestPass();
    },
    beginTurn() {
      resting = false;
      resume(host.metrics());
      turnPin = { behavior: "smooth", since: deps.now() };
      requestPass();
    },
    landOnLatest({ turn }) {
      resting = false;
      resume(host.metrics());
      // A turn that is already being brought to the top (a message sent as the conversation opened) is not undone by landing.
      if (turn) turnPin = { behavior: "auto", since: deps.now() };
      requestPass();
    },
    jump() {
      const metrics = host.metrics();
      turnPin = null;
      resting = false;
      resume(metrics);
      if (metrics.below > 0) move(liveEdgeTop(metrics, options.liveMargin), "smooth");
    },
    cancel() {
      if (frame !== null) deps.cancelFrame(frame);
      if (smoothTimer !== null) deps.clearTimeout(smoothTimer);
      if (quietTimer !== null) deps.clearTimeout(quietTimer);
      frame = null; smoothTimer = null; quietTimer = null;
      // The scroller went away, and nothing about its view applies to the next one: that one has no room reserved yet, no place
      // to keep and no reader to defer to (the thread it holds lands itself).
      turnPin = null; pinned = null; anchor = null; resting = false;
      room = -1; lastTop = 0; lastHeight = 0; programmaticUntil = 0; smoothUntil = 0;
      heldScrollbar = false; heldSelection = false;
      following = true; pausedBy = null;
      const idle: ScrollSnapshot = { following: true, jumpVisible: false, pausedBy: null };
      if (!sameSnapshot(snapshot, idle)) { snapshot = idle; listeners.forEach((listener) => listener()); }
    },
  };
}
