"use client";
import { useEffect, useSyncExternalStore } from "react";
import { CircleAlert, CircleCheck, Undo2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useNow } from "@/features/delivery/use-now";
import { deferredMerge, secondsLeft } from "@/lib/deferred-merge";

const SUCCESS_MS = 6000;
const editable = (target: EventTarget | null) => target instanceof HTMLElement && (target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName));

/**
 * What a merge says outside the panel: while its undo window is open a notice with an Undo button (and Ctrl/Cmd+Z, except while
 * you are typing, where that key undoes your text), and afterwards whether it merged or why it did not. A failure stays until
 * dismissed. The merge itself lives in `lib/deferred-merge.ts`, so it is shown wherever you are.
 */
export function DeliveryToaster() {
  const { pending, notices } = useSyncExternalStore(deferredMerge.subscribe, deferredMerge.getSnapshot, deferredMerge.getSnapshot);
  const waiting = pending.filter((item) => item.phase === "waiting");
  const now = useNow(waiting.length > 0, 250);

  useEffect(() => {
    if (!waiting.length) return;
    const undo = (event: KeyboardEvent) => {
      if (event.key.toLowerCase() !== "z" || !(event.metaKey || event.ctrlKey) || event.shiftKey || event.altKey || editable(event.target)) return;
      const latest = deferredMerge.getSnapshot().pending.findLast((item) => item.phase === "waiting");
      if (!latest) return;
      event.preventDefault();
      deferredMerge.cancel(latest.key);
    };
    window.addEventListener("keydown", undo);
    return () => window.removeEventListener("keydown", undo);
  }, [waiting.length]);

  const successes = notices.filter((notice) => notice.tone === "success").map((notice) => notice.id).join(",");
  useEffect(() => {
    const ids = successes ? successes.split(",").map(Number) : [];
    const timers = ids.map((id) => setTimeout(() => deferredMerge.dismiss(id), SUCCESS_MS));
    return () => timers.forEach(clearTimeout);
  }, [successes]);

  if (!waiting.length && !notices.length) return null;
  return <div className="pointer-events-none fixed inset-x-0 bottom-24 z-[60] flex flex-col items-center gap-2 px-4 sm:bottom-28" data-delivery-toasts>
    {waiting.map((item) => <div key={item.key} role="status" className="pointer-events-auto flex w-full max-w-md items-center gap-3 rounded-xl border bg-popover px-3.5 py-2.5 text-[13px] leading-5 text-popover-foreground shadow-[0_12px_32px_-8px_rgb(0_0_0/0.25)] motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-bottom-2">
      <span className="min-w-0 flex-1">{item.label}<span aria-hidden="true" className="text-muted-foreground"> · in {secondsLeft(item.endsAt, now)}s</span></span>
      <Button type="button" variant="outline" size="sm" className="shrink-0 pointer-coarse:min-h-11" onClick={() => deferredMerge.cancel(item.key)}><Undo2 />Undo</Button>
    </div>)}
    {notices.map((notice) => <div key={notice.id} role={notice.tone === "error" ? "alert" : "status"} className="pointer-events-auto flex w-full max-w-md items-start gap-2.5 rounded-xl border bg-popover px-3.5 py-2.5 text-[13px] leading-5 text-popover-foreground shadow-[0_12px_32px_-8px_rgb(0_0_0/0.25)] motion-safe:animate-in motion-safe:fade-in">
      {notice.tone === "error" ? <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden="true" /> : <CircleCheck className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />}
      <span className="min-w-0 flex-1">{notice.text}</span>
      <Button type="button" variant="ghost" size="icon-sm" className="-my-1 -mr-1.5 size-7 shrink-0 pointer-coarse:size-11" aria-label="Dismiss" onClick={() => deferredMerge.dismiss(notice.id)}><X className="size-3.5" /></Button>
    </div>)}
  </div>;
}
