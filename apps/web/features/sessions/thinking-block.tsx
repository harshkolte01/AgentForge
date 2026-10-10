"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ChevronRight } from "lucide-react";

export function ThinkingBlock({ content, isThinking = false, onToggle }: { content: string; isThinking?: boolean; onToggle?: () => void }) {
  const id = useId();
  const [expanded, setExpanded] = useState<boolean | null>(null);
  const open = expanded ?? isThinking;
  const viewport = useRef<HTMLDivElement>(null);
  const following = useRef(true);

  useEffect(() => {
    if (open && isThinking && following.current && viewport.current) {
      viewport.current.scrollTop = viewport.current.scrollHeight;
    }
  }, [content, open, isThinking]);

  if (!content.trim() && !isThinking) return null;

  return <div className="min-w-0 text-sm text-muted-foreground">
    <button
      type="button"
      aria-label={isThinking ? "Thinking" : "Thought process"}
      aria-expanded={open}
      aria-controls={id}
      className="group/thinking inline-flex min-h-11 max-w-full items-center gap-2 text-left outline-none focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-ring sm:min-h-9"
      onClick={() => { setExpanded(!open); onToggle?.(); }}
    >
      <ChevronRight className={`size-3.5 shrink-0 transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`} aria-hidden="true" />
      <span role="status" className={`${isThinking ? "thinking-shimmer" : ""} transition-colors group-hover/thinking:text-foreground motion-reduce:transition-none`}>{isThinking ? "Thinking" : "Thought process"}</span>
    </button>
    <div id={id} hidden={!open} className="ml-1.5 border-l pl-4">
      <div ref={viewport} onScroll={(event) => { const element = event.currentTarget; following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 32; }} className="max-h-64 overflow-y-auto overscroll-contain whitespace-pre-wrap pb-1 pr-3 leading-6 [overflow-wrap:anywhere]" tabIndex={open && content ? 0 : undefined} role="region" aria-label="Model thought process">
        {content}
      </div>
    </div>
  </div>;
}
