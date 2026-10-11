"use client";
import { ArrowDown } from "lucide-react";
import { useScrollController, useScrollSnapshot } from "@/features/sessions/use-conversation-scroll";

/**
 * "Jump to latest": a view of the conversation's scroll controller. It appears above the composer only when the reader is not
 * following and real content (not just reserved room) lies below the visible area.
 */
export function ScrollToBottom() {
  const controller = useScrollController();
  const { jumpVisible } = useScrollSnapshot(controller);
  if (!jumpVisible) return null;
  return <button type="button" data-conversation-jump aria-label="Jump to latest message" title="Jump to latest message"
    className="absolute -top-12 left-1/2 z-10 flex size-8 -translate-x-1/2 items-center justify-center rounded-full border bg-background text-muted-foreground shadow-md outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 pointer-coarse:size-11 pointer-coarse:-top-14 motion-safe:animate-in motion-safe:fade-in"
    onClick={(event) => {
      controller.jump();
      // The button leaves with the jump; a keyboard user lands back in the message field instead of on the page.
      if (event.detail === 0) document.getElementById("conversation-goal")?.focus({ preventScroll: true });
    }}><ArrowDown className="size-4" aria-hidden="true" /></button>;
}
