"use client";
import { useEffect, useRef, useState } from "react";
import { Check, Copy, TriangleAlert } from "lucide-react";
import { copyLabels, copyToClipboard, type CopyState } from "@/features/sessions/copy-feedback";

/** Icon button that copies text and briefly confirms. A refused clipboard is stated too ("Couldn't copy"), announced, and clears itself. */
export function CopyButton({ text, label = "Copy", showLabel = false, className = "" }: { text: string; label?: string; showLabel?: boolean; className?: string }) {
  const [state, setState] = useState<CopyState>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    const next: CopyState = (await copyToClipboard(text)) ? "copied" : "failed";
    setState(next);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), copyLabels(next, label).clearAfter ?? 0);
  }
  const labels = copyLabels(state, label);
  const Icon = state === "copied" ? Check : state === "failed" ? TriangleAlert : Copy;
  return <button type="button" onClick={() => void copy()} aria-label={labels.name} title={labels.name} data-copy-state={state} className={`inline-flex shrink-0 items-center justify-center gap-1.5 rounded-lg text-xs text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 motion-reduce:transition-none ${showLabel ? "h-7 px-2" : "size-8"} ${className}`}>
    <Icon className="size-3.5" aria-hidden="true" />
    {showLabel ? <span>{labels.visible}</span> : null}
    <span role="status" aria-live="polite" className="sr-only">{labels.status}</span>
  </button>;
}
