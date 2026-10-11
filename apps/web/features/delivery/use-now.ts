"use client";
import { useEffect, useState } from "react";

/** The current time, re-read every `ms` while `active` (a countdown, "synced 2m ago"); a fixed value otherwise. */
export function useNow(active: boolean, ms: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    // Catch up at once (the value may be old when this starts), then follow the clock.
    const first = setTimeout(() => setNow(Date.now()), 0);
    const timer = setInterval(() => setNow(Date.now()), ms);
    return () => { clearTimeout(first); clearInterval(timer); };
  }, [active, ms]);
  return now;
}
