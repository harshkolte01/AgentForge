"use client";
import { forwardRef, useCallback, useEffect, useRef, useState, type ComponentPropsWithoutRef, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import { readSidebarWidth, SIDEBAR_WIDTH_KEY, writeSidebarWidth } from "@/lib/preferences";

const defaultWidth = 240;
const minWidth = 220;
const maxWidth = 360;
function bounded(value: number) { return Math.min(maxWidth, Math.max(minWidth, Math.round(value))); }
function storedWidth(value: string | null) { const parsed = value?.trim() ? Number(value) : NaN; return Number.isFinite(parsed) ? bounded(parsed) : defaultWidth; }
type Drag = { id: number; startX: number; startWidth: number; nextWidth: number; element: HTMLDivElement; userSelect: string; cursor: string; frame: number | null };

export const ResizableSidebar = forwardRef<HTMLElement, ComponentPropsWithoutRef<"aside"> & { collapsed: boolean }>(function ResizableSidebar({ children, className = "", collapsed, ...props }, ref) {
  // The saved width is read before the first paint, so the thread never lays out once at the default and again at the saved width.
  // (The workspace renders only in the browser, behind the session check, so there is no server markup to disagree with.)
  const [width, setWidth] = useState(() => typeof window === "undefined" ? defaultWidth : storedWidth(readSidebarWidth()));
  const [dragging, setDragging] = useState(false);
  const widthRef = useRef(width);
  const drag = useRef<Drag | null>(null);
  const setCurrent = useCallback((value: number) => { const next = bounded(value); widthRef.current = next; setWidth(next); return next; }, []);
  const save = useCallback((value: number) => writeSidebarWidth(value), []);
  const finish = useCallback((commit: boolean) => {
    const current = drag.current;
    if (!current) return;
    drag.current = null;
    if (current.frame !== null) cancelAnimationFrame(current.frame);
    document.documentElement.style.userSelect = current.userSelect;
    document.documentElement.style.cursor = current.cursor;
    if (current.element.hasPointerCapture(current.id)) current.element.releasePointerCapture(current.id);
    const next = setCurrent(commit ? current.nextWidth : current.startWidth);
    if (commit) save(next);
    setDragging(false);
  }, [save, setCurrent]);
  useEffect(() => {
    const resize = () => { if (!window.matchMedia("(min-width: 1024px)").matches) finish(false); };
    const storage = (event: StorageEvent) => { if (event.key === SIDEBAR_WIDTH_KEY && !drag.current) setCurrent(storedWidth(event.newValue)); };
    const escape = (event: globalThis.KeyboardEvent) => { if (event.key === "Escape" && drag.current) { event.preventDefault(); finish(false); } };
    const blur = () => finish(false);
    window.addEventListener("resize", resize);
    window.addEventListener("storage", storage);
    window.addEventListener("keydown", escape);
    window.addEventListener("blur", blur);
    return () => { window.removeEventListener("resize", resize); window.removeEventListener("storage", storage); window.removeEventListener("keydown", escape); window.removeEventListener("blur", blur); finish(false); };
  }, [finish, setCurrent]);
  useEffect(() => { if (collapsed) { const frame = requestAnimationFrame(() => finish(false)); return () => cancelAnimationFrame(frame); } }, [collapsed, finish]);
  function start(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || !event.isPrimary || drag.current || !window.matchMedia("(min-width: 1024px)").matches) return;
    event.preventDefault();
    event.currentTarget.focus();
    finish(false);
    drag.current = { id: event.pointerId, startX: event.clientX, startWidth: widthRef.current, nextWidth: widthRef.current, element: event.currentTarget, userSelect: document.documentElement.style.userSelect, cursor: document.documentElement.style.cursor, frame: null };
    event.currentTarget.setPointerCapture(event.pointerId);
    document.documentElement.style.userSelect = "none";
    document.documentElement.style.cursor = "col-resize";
    setDragging(true);
  }
  function move(event: PointerEvent<HTMLDivElement>) {
    const current = drag.current;
    if (!current || current.id !== event.pointerId) return;
    current.nextWidth = bounded(current.startWidth + event.clientX - current.startX);
    if (current.frame === null) current.frame = requestAnimationFrame(() => { current.frame = null; if (drag.current === current) setCurrent(current.nextWidth); });
  }
  function keyboard(event: KeyboardEvent<HTMLDivElement>) {
    let next: number;
    if (event.key === "ArrowLeft") next = widthRef.current - (event.shiftKey ? 24 : 8);
    else if (event.key === "ArrowRight") next = widthRef.current + (event.shiftKey ? 24 : 8);
    else if (event.key === "Home") next = minWidth;
    else if (event.key === "End") next = maxWidth;
    else if (event.key === "Enter") next = defaultWidth;
    else return;
    event.preventDefault(); finish(false); save(setCurrent(next));
  }
  return <aside {...props} ref={ref} style={{ ...props.style, "--sidebar-width": `${width}px` } as CSSProperties} className={`w-60 max-w-[calc(100vw-2rem)] lg:relative lg:w-[var(--sidebar-width)] lg:max-w-none ${className}`}>
    {children}
    <div role="separator" aria-label="Resize sidebar" aria-orientation="vertical" aria-controls={props.id} aria-valuemin={minWidth} aria-valuemax={maxWidth} aria-valuenow={width} aria-valuetext={`${width} pixels`} aria-describedby="sidebar-resize-help" tabIndex={collapsed ? -1 : 0} onPointerDown={start} onPointerMove={move} onPointerUp={(event) => { if (drag.current?.id === event.pointerId) finish(true); }} onPointerCancel={(event) => { if (drag.current?.id === event.pointerId) finish(false); }} onLostPointerCapture={(event) => { if (drag.current?.id === event.pointerId) finish(false); }} onKeyDown={keyboard} onDoubleClick={() => { finish(false); save(setCurrent(defaultWidth)); }} className={`absolute inset-y-0 -right-1 z-50 hidden w-2 touch-none cursor-col-resize outline-none before:absolute before:inset-y-0 before:left-1 before:w-px hover:before:bg-foreground/25 focus-visible:before:bg-foreground/50 lg:block ${dragging ? "before:bg-foreground/35" : ""}`} />
    <span id="sidebar-resize-help" className="sr-only">Use arrow keys to resize. Home and End set the minimum and maximum. Enter or double-click resets the width.</span>
  </aside>;
});
