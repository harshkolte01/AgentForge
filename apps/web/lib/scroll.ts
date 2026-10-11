/**
 * The browser side of the conversation scroll controller (`scroll-controller.ts`): measuring the scroller, finding the reader's
 * place, writing the reserved room. The last turn reserves blank room below it, so the document end is not the content end.
 * Kept thin and free of rules: the rules are the controller's and run under test; this is covered by browser checks, for which
 * the markers below are the stable hooks.
 */
import type { Anchor, ScrollHost, ScrollMetrics } from "@/lib/scroll-controller";

export const scrollerMarker = "[data-conversation-scroll]";
const endMarker = "[data-conversation-end]";
const composerMarker = "[data-conversation-composer]";
const turnMarker = "[data-conversation-turn]";
/** Rows (messages, the live turn) that can serve as the reader's place. Set on the element, value = a stable id. */
const anchorMarker = "[data-scroll-anchor]";
/** The CSS custom property the latest turn's minimum height reads. */
export const roomProperty = "--conversation-room";

function composerOf(scroller: HTMLElement, end: HTMLElement | null): HTMLElement | null {
  const composer = scroller.querySelector<HTMLElement>(composerMarker) ?? (scroller.lastElementChild as HTMLElement | null);
  return composer && !(end && composer.contains(end)) ? composer : null;
}

/** Pixels of real content hidden below the visible area (the sticky composer covers the bottom of the scroller). */
export function contentBelow(scroller: HTMLElement): number {
  const end = scroller.querySelector<HTMLElement>(endMarker);
  if (!end) return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
  const covered = composerOf(scroller, end)?.offsetHeight ?? 0;
  return end.getBoundingClientRect().bottom - (scroller.getBoundingClientRect().bottom - covered);
}

export function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

type Mark = { el: Element; offset: number };
/** The reader's place: the first visible row and, inside it, the nested elements down to the one at the top edge (deepest first). */
type Place = { chain: Mark[]; id: string | null; idOffset: number };

const visible = (el: Element) => el.getClientRects().length > 0;

/** First index whose bottom edge is below `edge`, by bisection (rows stack top to bottom). */
function firstBelow(items: ArrayLike<Element>, edge: number): number {
  let low = 0, high = items.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (items[middle].getBoundingClientRect().bottom > edge) high = middle; else low = middle + 1;
  }
  return low;
}

/** The place of `start` and of the first nested element at the top edge, so a change inside a tall row is followed too. */
function placeOf(scroller: HTMLElement, start: Element, viewTop: number): Place {
  const id = start.getAttribute("data-scroll-anchor");
  const chain: Mark[] = [{ el: start, offset: start.getBoundingClientRect().top - viewTop }];
  let current = start;
  for (let depth = 0; depth < 3 && current.children.length; depth++) {
    const index = firstBelow(current.children, viewTop + 1);
    const child = current.children[index];
    if (!child || !visible(child)) break;
    chain.unshift({ el: child, offset: child.getBoundingClientRect().top - viewTop });
    current = child;
  }
  return { chain, id, idOffset: chain[chain.length - 1].offset };
}

/** A host for `scroller`. Safe to create before the page has laid out; every method tolerates a missing element. */
export function createDomScrollHost(scroller: HTMLElement): ScrollHost {
  let roomFrame = 0;
  let pendingRoom = -1;
  return {
    metrics(): ScrollMetrics {
      const end = scroller.querySelector<HTMLElement>(endMarker);
      return { top: scroller.scrollTop, height: scroller.scrollHeight, viewport: scroller.clientHeight, below: contentBelow(scroller), composer: composerOf(scroller, end)?.offsetHeight ?? 0 };
    },
    scrollTo(top, behavior) { scroller.scrollTo({ top, behavior }); },
    captureAnchor(): Anchor | null {
      const rows = scroller.querySelectorAll<HTMLElement>(anchorMarker);
      if (!rows.length) return null;
      const viewTop = scroller.getBoundingClientRect().top;
      // Past every row (scrolled into the reserved room) the last row is the place.
      const index = Math.min(firstBelow(rows, viewTop + 1), rows.length - 1);
      return placeOf(scroller, rows[index], viewTop);
    },
    anchorShift(anchor) {
      const place = anchor as Place;
      const viewTop = scroller.getBoundingClientRect().top;
      for (const mark of place.chain) {
        if (mark.el.isConnected && visible(mark.el)) return mark.el.getBoundingClientRect().top - viewTop - mark.offset;
      }
      // The row itself was replaced (the live turn by the saved message): find its successor by id.
      const successor = place.id ? scroller.querySelector(`[data-scroll-anchor="${CSS.escape(place.id)}"]`) : null;
      return successor && visible(successor) ? successor.getBoundingClientRect().top - viewTop - place.idOffset : null;
    },
    turnTop(gap) {
      const turn = scroller.querySelector<HTMLElement>(turnMarker);
      if (!turn) return null;
      return turn.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - gap;
    },
    setRoom(px) {
      // Written on the next frame: changing the room resizes the thread, which must not happen inside a ResizeObserver callback.
      pendingRoom = px;
      if (roomFrame) return;
      roomFrame = requestAnimationFrame(() => { roomFrame = 0; scroller.style.setProperty(roomProperty, `${pendingRoom}px`); });
    },
    reducedMotion: prefersReducedMotion,
  };
}

/** The anchor for a specific element (a disclosure toggle), in the same shape `captureAnchor` makes. */
export function anchorForElement(scroller: HTMLElement, element: Element): Anchor {
  const viewTop = scroller.getBoundingClientRect().top;
  const row = element.closest(anchorMarker);
  return { chain: [{ el: element, offset: element.getBoundingClientRect().top - viewTop }], id: row?.getAttribute("data-scroll-anchor") ?? null, idOffset: row ? row.getBoundingClientRect().top - viewTop : 0 } satisfies Place;
}

/**
 * The controller's host for whichever scroller is mounted. The controller outlives its scroller: the subtree holding it can be
 * replaced (a conversation opening, a first message creating one) while the workspace stays, so the host follows `mount`, never
 * an element found once. Until one is mounted every call is a harmless no-op.
 */
export function createScrollerBridge(): { host: ScrollHost; mount(element: HTMLElement): () => void } {
  let current: { element: HTMLElement; host: ScrollHost } | null = null;
  const host = () => current?.host ?? null;
  return {
    /** Makes `element` the scroller. The returned function lets go of it, unless another scroller has taken its place since. */
    mount(element) {
      const mounted = { element, host: createDomScrollHost(element) };
      current = mounted;
      return () => { if (current === mounted) current = null; };
    },
    host: {
      metrics: () => host()?.metrics() ?? { top: 0, height: 0, viewport: 0, below: 0, composer: 0 },
      scrollTo: (top, behavior) => host()?.scrollTo(top, behavior),
      captureAnchor: () => host()?.captureAnchor() ?? null,
      anchorShift: (anchor) => host()?.anchorShift(anchor) ?? null,
      turnTop: (gap) => host()?.turnTop(gap) ?? null,
      setRoom: (px) => host()?.setRoom(px),
      reducedMotion: prefersReducedMotion,
    },
  };
}
