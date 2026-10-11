"use client";
import { createContext, useContext, useEffect, useState, useSyncExternalStore } from "react";
import { anchorForElement, createDomScrollHost, scrollerMarker } from "@/lib/scroll";
import { createScrollController, isScrollbarPointerDown, keyScrollDirection, type ScrollController, type ScrollSnapshot } from "@/lib/scroll-controller";

/**
 * The conversation's single scroll controller, wired to the real scroller. The workspace owns it (one scroller, one controller)
 * and shares it through context: the thread tells it what happened (a message was sent, a conversation opened, earlier messages
 * are about to load), the jump button reads and commands it. Rules live in `lib/scroll-controller.ts`; this file only translates
 * browser events into its inputs and keeps the observers and listeners tidy.
 */
const idleSnapshot: ScrollSnapshot = { following: true, jumpVisible: false, pausedBy: null };
const inertController: ScrollController = {
  getSnapshot: () => idleSnapshot, subscribe: () => () => {}, input() {}, scrollbarDown() {}, pointerUp() {}, selection() {}, toggle() {}, holdPlace() {},
  scrolled() {}, layoutChanged() {}, requestPass() {}, shift() {}, beginTurn() {}, landOnLatest() {}, jump() {}, cancel() {},
};

export const ConversationScrollContext = createContext<ScrollController>(inertController);
export const useScrollController = () => useContext(ConversationScrollContext);
export function useScrollSnapshot(controller: ScrollController): ScrollSnapshot {
  return useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
}

const editableSelector = "input, textarea, select, [contenteditable=''], [contenteditable='true']";
const activatingSelector = "button, a[href], summary, [role='button'], [role='menuitem'], [role='tab'], [role='option'], [role='switch'], [role='checkbox']";

/** A scrollable element between the pointer and the scroller that will take this wheel/drag movement itself (a log with its own scrollbar). */
function innerScrollerConsumes(target: EventTarget | null, scroller: HTMLElement, deltaY: number): boolean {
  for (let node = target instanceof Element ? target : null; node && node !== scroller; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (!/(auto|scroll)/.test(style.overflowY) || node.scrollHeight <= node.clientHeight + 1) continue;
    return deltaY < 0 ? node.scrollTop > 0 : node.scrollTop + node.clientHeight < node.scrollHeight - 1;
  }
  return false;
}

/** Creates the controller for the scroller element marked `data-conversation-scroll`, which the workspace always renders. */
export function useConversationScroll(): ScrollController {
  const [controller] = useState(() => createScrollController({
    // The host reads the element lazily: the controller exists before the page has laid out.
    host: lazyHost(() => document.querySelector<HTMLElement>(scrollerMarker)),
    now: () => performance.now(),
    requestFrame: (callback) => requestAnimationFrame(callback),
    cancelFrame: (handle) => cancelAnimationFrame(handle as number),
    setTimeout: (callback, ms) => window.setTimeout(callback, ms),
    clearTimeout: (handle) => window.clearTimeout(handle as number),
  }));
  useEffect(() => {
    const scroller = document.querySelector<HTMLElement>(scrollerMarker);
    if (!scroller) return;
    const root = scroller.closest<HTMLElement>("[data-conversation-root]");
    let scrollFrame = 0;
    let touchY = 0;
    let selecting = false;

    const onScroll = () => {
      // One report per frame: a fling fires many scroll events, and each report may measure the page.
      if (scrollFrame) return;
      scrollFrame = requestAnimationFrame(() => { scrollFrame = 0; controller.scrolled(); });
    };
    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey || Math.abs(event.deltaY) < Math.abs(event.deltaX)) return;
      if (innerScrollerConsumes(event.target, scroller, event.deltaY)) return;
      controller.input("wheel", event.deltaY);
    };
    const onTouchStart = (event: TouchEvent) => { touchY = event.touches[0]?.clientY ?? 0; };
    const onTouchMove = (event: TouchEvent) => {
      const y = event.touches[0]?.clientY ?? touchY;
      // A finger moving up scrolls toward newer content.
      const delta = touchY - y;
      touchY = y;
      if (delta !== 0 && !innerScrollerConsumes(event.target, scroller, delta)) controller.input("touch", delta);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.altKey) return;
      const target = event.target instanceof Element ? event.target : null;
      // Keys scroll the page when focus is on it (the scroller, something inside it, or the bare page), never from a menu or dialog elsewhere.
      if (target && target !== document.body && target !== document.documentElement && !scroller.contains(target)) return;
      const direction = keyScrollDirection(event.key, event.shiftKey, {
        editable: !!target?.closest(editableSelector) || (target instanceof HTMLElement && target.isContentEditable),
        activates: !!target?.closest(activatingSelector),
      });
      if (direction) controller.input("keyboard", direction === "up" ? -1 : 1);
    };
    const onPointerDown = (event: PointerEvent) => {
      if (isScrollbarPointerDown({ targetIsScroller: event.target === scroller, pointerType: event.pointerType, clientX: event.clientX, right: scroller.getBoundingClientRect().right, scrollbarWidth: scroller.offsetWidth - scroller.clientWidth })) controller.scrollbarDown();
    };
    const onPointerUp = () => controller.pointerUp();
    const onSelection = () => {
      const selection = document.getSelection();
      const thread = scroller.querySelector("[data-conversation-thread]");
      const active = !!selection && !selection.isCollapsed && !!thread && (thread.contains(selection.anchorNode) || thread.contains(selection.focusNode));
      if (active === selecting) return;
      selecting = active;
      controller.selection(active);
    };
    // Before React handles the click: the toggle's place must be read while the layout is still the old one.
    const onClickCapture = (event: MouseEvent) => {
      const toggle = event.target instanceof Element ? event.target.closest("[data-scroll-anchor-toggle]") : null;
      if (toggle && scroller.contains(toggle)) controller.toggle(anchorForElement(scroller, toggle));
    };

    // Stable hooks for browser checks: what the controller currently believes.
    const publish = () => {
      const snapshot = controller.getSnapshot();
      scroller.dataset.following = String(snapshot.following);
      scroller.dataset.pausedBy = snapshot.pausedBy ?? "";
    };
    publish();
    const unsubscribe = controller.subscribe(publish);

    scroller.addEventListener("scroll", onScroll, { passive: true });
    scroller.addEventListener("wheel", onWheel, { passive: true });
    scroller.addEventListener("touchstart", onTouchStart, { passive: true });
    scroller.addEventListener("touchmove", onTouchMove, { passive: true });
    scroller.addEventListener("pointerdown", onPointerDown);
    scroller.addEventListener("click", onClickCapture, true);
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("selectionchange", onSelection);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);

    // One observer for everything whose size matters: the scroller itself, the thread, the queue and the composer.
    const observer = new ResizeObserver(() => controller.layoutChanged());
    const observed = new Set<Element>();
    const observeChildren = () => {
      for (const child of Array.from(scroller.children)) if (!observed.has(child)) { observed.add(child); observer.observe(child); }
      for (const child of observed) if (!child.isConnected) { observed.delete(child); observer.unobserve(child); }
    };
    observer.observe(scroller);
    observeChildren();
    const children = new MutationObserver(observeChildren);
    children.observe(scroller, { childList: true });

    // A mobile keyboard shrinks the visual viewport, not always the layout viewport: size the shell to what is actually visible.
    const viewport = window.visualViewport;
    const fit = () => {
      if (!viewport || !root) return;
      const shrunk = viewport.scale === 1 && window.innerHeight - viewport.height > 80;
      if (shrunk) { root.style.setProperty("--visible-height", `${viewport.height}px`); root.style.setProperty("--visible-top", `${viewport.offsetTop}px`); }
      else { root.style.removeProperty("--visible-height"); root.style.removeProperty("--visible-top"); }
      controller.requestPass();
    };
    viewport?.addEventListener("resize", fit);
    viewport?.addEventListener("scroll", fit);
    fit();

    return () => {
      cancelAnimationFrame(scrollFrame);
      unsubscribe();
      controller.cancel();
      scroller.removeEventListener("scroll", onScroll);
      scroller.removeEventListener("wheel", onWheel);
      scroller.removeEventListener("touchstart", onTouchStart);
      scroller.removeEventListener("touchmove", onTouchMove);
      scroller.removeEventListener("pointerdown", onPointerDown);
      scroller.removeEventListener("click", onClickCapture, true);
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("selectionchange", onSelection);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
      observer.disconnect();
      children.disconnect();
      viewport?.removeEventListener("resize", fit);
      viewport?.removeEventListener("scroll", fit);
      if (root) { root.style.removeProperty("--visible-height"); root.style.removeProperty("--visible-top"); }
    };
  }, [controller]);
  return controller;
}

/** A host that resolves the scroller on each call, so the controller can be built before the element exists. */
function lazyHost(get: () => HTMLElement | null) {
  let cached: { element: HTMLElement; host: ReturnType<typeof createDomScrollHost> } | null = null;
  const host = () => {
    const element = get();
    if (!element) return null;
    if (cached?.element !== element) cached = { element, host: createDomScrollHost(element) };
    return cached.host;
  };
  return {
    metrics: () => host()?.metrics() ?? { top: 0, height: 0, viewport: 0, below: 0, composer: 0 },
    scrollTo: (top: number, behavior: "auto" | "smooth") => host()?.scrollTo(top, behavior),
    captureAnchor: () => host()?.captureAnchor() ?? null,
    anchorShift: (anchor: unknown) => host()?.anchorShift(anchor) ?? null,
    turnTop: (gap: number) => host()?.turnTop(gap) ?? null,
    setRoom: (px: number) => host()?.setRoom(px),
    reducedMotion: () => (typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches),
  };
}
