/** Scroll math for the conversation. The last turn reserves blank room below it, so the document end is not the content end. */
const endMarker = "[data-conversation-end]";

/** Pixels of real content hidden below the visible area (the sticky composer covers the bottom of the scroller). */
export function contentBelow(scroller: HTMLElement): number {
  const end = scroller.querySelector<HTMLElement>(endMarker);
  if (!end) return scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
  const composer = scroller.lastElementChild as HTMLElement | null;
  const covered = composer && !composer.contains(end) ? composer.offsetHeight : 0;
  return end.getBoundingClientRect().bottom - (scroller.getBoundingClientRect().bottom - covered);
}

export function prefersReducedMotion(): boolean {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** Brings the end of the latest content into view just above the composer. */
export function scrollToContentEnd(scroller: HTMLElement, behavior: ScrollBehavior = "auto") {
  const below = contentBelow(scroller);
  if (below > 0) scroller.scrollTo({ top: scroller.scrollTop + below + 16, behavior: prefersReducedMotion() ? "auto" : behavior });
}

/** Places an element near the top of the scroller, with a small gap, so a new turn reads from its start. */
export function scrollTurnToTop(scroller: HTMLElement, turn: HTMLElement, gap = 20, behavior: ScrollBehavior = "smooth") {
  const top = turn.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - gap;
  scroller.scrollTo({ top: Math.max(0, top), behavior: prefersReducedMotion() ? "auto" : behavior });
}
