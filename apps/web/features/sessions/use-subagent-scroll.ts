"use client";

import { useLayoutEffect, useRef, useState } from "react";
import { createSubagentScroll } from "./subagent-scroll";

export function useSubagentScroll(initiallyFollowing: boolean) {
  const viewport = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [scroll] = useState(() => createSubagentScroll(initiallyFollowing));
  const [showLatest, setShowLatest] = useState(false);

  useLayoutEffect(() => {
    const node = viewport.current;
    const body = content.current;
    if (!node || !body) return;
    let frame = 0;
    let touchY = 0;
    const update = () => setShowLatest(scroll.jumpVisible(node));
    const pause = () => { scroll.pause(); update(); };
    const sync = () => {
      frame = 0;
      const top = scroll.layout(node);
      if (top !== null) node.scrollTop = top;
      update();
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(sync); };
    const onScroll = () => { scroll.scrolled(node); update(); };
    const onWheel = (event: WheelEvent) => {
      if (event.ctrlKey) return;
      event.stopPropagation();
      if (event.deltaY < 0) pause();
    };
    const onTouchStart = (event: TouchEvent) => { touchY = event.touches[0]?.clientY ?? 0; };
    const onTouchMove = (event: TouchEvent) => {
      event.stopPropagation();
      const next = event.touches[0]?.clientY ?? touchY;
      if (next > touchY) pause();
      touchY = next;
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey || event.isComposing) return;
      if (!["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(event.key)) return;
      event.stopPropagation();
      if (["ArrowUp", "PageUp", "Home"].includes(event.key) || (event.key === " " && event.shiftKey)) pause();
    };
    const onPointer = (event: PointerEvent) => { if (event.target === node) pause(); };
    const onDisclosure = (event: MouseEvent) => {
      if (event.target instanceof Element && event.target.closest("summary, [data-scroll-anchor-toggle]")) pause();
    };
    const onSelection = () => {
      const selection = document.getSelection();
      if (selection && !selection.isCollapsed && (node.contains(selection.anchorNode) || node.contains(selection.focusNode))) pause();
    };
    const observer = new ResizeObserver(schedule);
    observer.observe(body);
    observer.observe(node);
    node.addEventListener("scroll", onScroll, { passive: true });
    node.addEventListener("wheel", onWheel, { passive: true });
    node.addEventListener("touchstart", onTouchStart, { passive: true });
    node.addEventListener("touchmove", onTouchMove, { passive: true });
    node.addEventListener("keydown", onKey);
    node.addEventListener("pointerdown", onPointer);
    node.addEventListener("click", onDisclosure, true);
    document.addEventListener("selectionchange", onSelection);
    schedule();
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frame);
      node.removeEventListener("scroll", onScroll);
      node.removeEventListener("wheel", onWheel);
      node.removeEventListener("touchstart", onTouchStart);
      node.removeEventListener("touchmove", onTouchMove);
      node.removeEventListener("keydown", onKey);
      node.removeEventListener("pointerdown", onPointer);
      node.removeEventListener("click", onDisclosure, true);
      document.removeEventListener("selectionchange", onSelection);
    };
  }, [scroll]);

  function jumpToLatest() {
    const node = viewport.current;
    if (!node) return;
    scroll.resume();
    node.scrollTop = scroll.layout(node) ?? node.scrollTop;
    setShowLatest(false);
  }
  return { viewportRef: viewport, contentRef: content, showLatest, jumpToLatest };
}
