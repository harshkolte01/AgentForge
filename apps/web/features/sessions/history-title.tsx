"use client";

import { useEffect, useRef, useState } from "react";
import "./session-history.css";

/** Measure actual overflow so short titles never fade, including after resizing. */
export function HistoryTitle({ title }: { title: string }) {
  const viewport = useRef<HTMLSpanElement>(null);
  const text = useRef<HTMLSpanElement>(null);
  const [overflow, setOverflow] = useState(0);

  useEffect(() => {
    const container = viewport.current;
    const content = text.current;
    if (!container || !content) return;
    const measure = () => setOverflow(Math.max(0, content.scrollWidth - container.clientWidth));
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    observer.observe(content);
    measure();
    return () => observer.disconnect();
  }, [title]);

  return <span ref={viewport} className="history-title" data-overflow={overflow > 1 || undefined}>
    <span ref={text} className="history-titleText">{title}</span>
  </span>;
}
