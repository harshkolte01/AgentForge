"use client";
import { useEffect, useState } from "react";
import { ArrowDown } from "lucide-react";
import { contentBelow, scrollToContentEnd } from "@/lib/scroll";

/** Appears above the composer when real content (not just reserved room) lies below the visible area. */
export function ScrollToBottom() {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const element = document.querySelector<HTMLElement>("[data-conversation-scroll]");
    if (!element) return;
    const update = () => setVisible(contentBelow(element) > 120);
    const frame = requestAnimationFrame(update);
    element.addEventListener("scroll", update, { passive: true });
    const observer = new ResizeObserver(update);
    observer.observe(element);
    if (element.firstElementChild) observer.observe(element.firstElementChild);
    return () => { cancelAnimationFrame(frame); element.removeEventListener("scroll", update); observer.disconnect(); };
  }, []);
  if (!visible) return null;
  return <button type="button" aria-label="Scroll to latest message" className="absolute -top-12 left-1/2 z-10 flex size-8 -translate-x-1/2 items-center justify-center rounded-full border bg-background text-muted-foreground shadow-md outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 motion-safe:animate-in motion-safe:fade-in" onClick={() => { const scroller = document.querySelector<HTMLElement>("[data-conversation-scroll]"); if (scroller) scrollToContentEnd(scroller, "smooth"); }}><ArrowDown className="size-4" aria-hidden="true" /></button>;
}
