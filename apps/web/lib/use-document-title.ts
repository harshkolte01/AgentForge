"use client";
import { useEffect } from "react";

/**
 * Keeps `document.title` in step with client-side state. Next re-applies its static metadata title on every
 * client navigation, so a one-shot assignment would be overwritten; the observer re-asserts ours while mounted.
 */
export function useDocumentTitle(title: string) {
  useEffect(() => {
    const apply = () => { if (document.title !== title) document.title = title; };
    apply();
    const observer = new MutationObserver(apply);
    observer.observe(document.head, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, [title]);
}
