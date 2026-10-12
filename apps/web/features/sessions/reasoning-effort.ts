"use client";
import { useEffect, useSyncExternalStore } from "react";
import { createReasoningEffortStore, type ReasoningEffortState } from "@/lib/reasoning-effort-store";
import { fetchReasoningEffort, saveReasoningEffort, type ReasoningEffort } from "@/lib/reasoning-effort-api";

/** What each effort is called and what it means for the person choosing. The server decides which ones a model offers. */
export const effortInfo: Record<ReasoningEffort, { label: string; hint: string }> = {
  none: { label: "None", hint: "No extra thinking. Fastest." },
  minimal: { label: "Minimal", hint: "Almost no thinking." },
  low: { label: "Low", hint: "Light thinking. Quick." },
  medium: { label: "Medium", hint: "Balanced speed and depth." },
  high: { label: "High", hint: "Thinks carefully. Default." },
  xhigh: { label: "Extra high", hint: "Deep thinking. Slower, costs more." },
  max: { label: "Max", hint: "Most thinking. Slowest, costliest." },
};

/** One copy for the page: every composer shares it, so switching conversations never reads it again. */
const store = createReasoningEffortStore({ fetch: () => fetchReasoningEffort(), save: saveReasoningEffort });
const idle: ReasoningEffortState = { effort: null, efforts: [], status: "idle" };
if (typeof window !== "undefined") for (const event of ["agentforge-session-expired", "agentforge-account-changed"]) window.addEventListener(event, () => store.reset());

/** The reasoning effort, read on first use. `choose` shows the new effort at once and rejects if it could not be saved. */
export function useReasoningEffort() {
  const state = useSyncExternalStore(store.subscribe, store.getSnapshot, () => idle);
  useEffect(() => { store.load(); }, []);
  return { ...state, choose: store.choose, retry: store.load };
}
