"use client";
import { useCallback, useSyncExternalStore } from "react";

/** Whether a media query matches now, following changes. `fallback` is what the server (and first paint) assumes. */
export function useMediaQuery(query: string, fallback = false): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    const list = window.matchMedia(query);
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, [query]);
  return useSyncExternalStore(subscribe, () => window.matchMedia(query).matches, () => fallback);
}
