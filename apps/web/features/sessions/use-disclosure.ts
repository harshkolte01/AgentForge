"use client";
import { useSyncExternalStore } from "react";
import { disclosure } from "@/lib/activity/disclosure";

/** A disclosure whose manual open/closed choice outlives re-renders, the run completing and in-page navigation. */
export function useDisclosure(key: string, defaultOpen: boolean): [open: boolean, toggle: () => void] {
  const manual = useSyncExternalStore(disclosure.subscribe, () => disclosure.get(key), () => undefined);
  const open = manual ?? defaultOpen;
  return [open, () => disclosure.set(key, !open)];
}
