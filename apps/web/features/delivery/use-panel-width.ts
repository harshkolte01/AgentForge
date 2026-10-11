"use client";
import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { DELIVERY_WIDTH_KEY, readDeliveryWidth, writeDeliveryWidth } from "@/lib/preferences";

export const defaultWidth = 448;
export const minWidth = 340;
const widest = 760;
/** Room always left for the conversation column and the sidebar beside the panel. */
const reserved = 640;

/** The widest the panel may be right now: never so wide that the conversation is squeezed out. */
export function maxWidth(viewport = typeof window === "undefined" ? 1440 : window.innerWidth) {
  return Math.max(minWidth, Math.min(widest, viewport - reserved));
}
export function bounded(value: number, viewport?: number) { return Math.min(maxWidth(viewport), Math.max(minWidth, Math.round(value))); }
export function parseWidth(value: string | null): number {
  const parsed = value?.trim() ? Number(value) : NaN;
  return Number.isFinite(parsed) ? bounded(parsed) : bounded(defaultWidth);
}

type Drag = { id: number; startX: number; startWidth: number; nextWidth: number; element: HTMLElement; userSelect: string; cursor: string; frame: number | null };

/**
 * The delivery panel's width, dragged from its left edge (dragging left widens it). The width is read before the first paint,
 * kept within bounds as the window changes, saved only when a drag or key press settles, and followed across tabs. Escape or a
 * lost pointer puts it back; the keyboard works too (arrows, Home, End, Enter to reset).
 */
export function usePanelWidth() {
  const [width, setWidth] = useState(() => parseWidth(typeof window === "undefined" ? null : readDeliveryWidth()));
  const [dragging, setDragging] = useState(false);
  const widthRef = useRef(width);
  const drag = useRef<Drag | null>(null);
  const apply = useCallback((value: number) => { const next = bounded(value); widthRef.current = next; setWidth(next); return next; }, []);
  const finish = useCallback((commit: boolean) => {
    const current = drag.current;
    if (!current) return;
    drag.current = null;
    if (current.frame !== null) cancelAnimationFrame(current.frame);
    document.documentElement.style.userSelect = current.userSelect;
    document.documentElement.style.cursor = current.cursor;
    if (current.element.hasPointerCapture(current.id)) current.element.releasePointerCapture(current.id);
    const next = apply(commit ? current.nextWidth : current.startWidth);
    if (commit) writeDeliveryWidth(next);
    setDragging(false);
  }, [apply]);

  useEffect(() => {
    const resize = () => { if (!drag.current) apply(widthRef.current); else finish(false); };
    const storage = (event: StorageEvent) => { if (event.key === DELIVERY_WIDTH_KEY && !drag.current) apply(parseWidth(event.newValue)); };
    const escape = (event: globalThis.KeyboardEvent) => { if (event.key === "Escape" && drag.current) { event.preventDefault(); event.stopPropagation(); finish(false); } };
    const blur = () => finish(false);
    window.addEventListener("resize", resize);
    window.addEventListener("storage", storage);
    window.addEventListener("keydown", escape, true);
    window.addEventListener("blur", blur);
    return () => { window.removeEventListener("resize", resize); window.removeEventListener("storage", storage); window.removeEventListener("keydown", escape, true); window.removeEventListener("blur", blur); finish(false); };
  }, [apply, finish]);

  const separator = {
    onPointerDown(event: PointerEvent<HTMLElement>) {
      if (event.button !== 0 || !event.isPrimary || drag.current || !window.matchMedia("(min-width: 1024px)").matches) return;
      event.preventDefault();
      event.currentTarget.focus();
      drag.current = { id: event.pointerId, startX: event.clientX, startWidth: widthRef.current, nextWidth: widthRef.current, element: event.currentTarget,
        userSelect: document.documentElement.style.userSelect, cursor: document.documentElement.style.cursor, frame: null };
      event.currentTarget.setPointerCapture(event.pointerId);
      document.documentElement.style.userSelect = "none";
      document.documentElement.style.cursor = "col-resize";
      setDragging(true);
    },
    onPointerMove(event: PointerEvent<HTMLElement>) {
      const current = drag.current;
      if (!current || current.id !== event.pointerId) return;
      current.nextWidth = bounded(current.startWidth - (event.clientX - current.startX));
      if (current.frame === null) current.frame = requestAnimationFrame(() => { current.frame = null; if (drag.current === current) apply(current.nextWidth); });
    },
    onPointerUp(event: PointerEvent<HTMLElement>) { if (drag.current?.id === event.pointerId) finish(true); },
    onPointerCancel(event: PointerEvent<HTMLElement>) { if (drag.current?.id === event.pointerId) finish(false); },
    onLostPointerCapture(event: PointerEvent<HTMLElement>) { if (drag.current?.id === event.pointerId) finish(false); },
    onDoubleClick() { finish(false); writeDeliveryWidth(apply(defaultWidth)); },
    onKeyDown(event: KeyboardEvent<HTMLElement>) {
      const step = event.shiftKey ? 24 : 8;
      let next: number;
      if (event.key === "ArrowLeft") next = widthRef.current + step;
      else if (event.key === "ArrowRight") next = widthRef.current - step;
      else if (event.key === "Home") next = minWidth;
      else if (event.key === "End") next = maxWidth();
      else if (event.key === "Enter") next = defaultWidth;
      else return;
      event.preventDefault();
      finish(false);
      writeDeliveryWidth(apply(next));
    },
  };
  return { width, dragging, separator, max: maxWidth() };
}
