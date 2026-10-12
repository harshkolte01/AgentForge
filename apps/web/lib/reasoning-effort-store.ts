/**
 * The person's reasoning effort, read once per page session and shared by every composer, so moving between conversations never
 * refetches it and a change shows everywhere at once. A failed read leaves no choice to offer (the menu simply has no effort
 * section) and is retried by the next `load()`.
 */
import type { ReasoningEffort, ReasoningEffortChoices } from "@/lib/reasoning-effort-api";

export type ReasoningEffortState = { effort: ReasoningEffort | null; efforts: readonly ReasoningEffort[]; status: "idle" | "loading" | "ready" | "error" | "saving" };
export type ReasoningEffortStore = {
  getSnapshot(): ReasoningEffortState;
  subscribe(listener: () => void): () => void;
  /** Reads the choices unless they are known or being read. */
  load(): void;
  /** Saves a new effort, showing it at once; a failure puts the previous one back and rejects. */
  choose(effort: ReasoningEffort): Promise<void>;
  /** Forgets everything (another account signed in). */
  reset(): void;
};

const NONE: ReasoningEffortState = { effort: null, efforts: [], status: "idle" };

export function createReasoningEffortStore(deps: { fetch: () => Promise<ReasoningEffortChoices>; save: (effort: ReasoningEffort) => Promise<ReasoningEffortChoices> }): ReasoningEffortStore {
  let state: ReasoningEffortState = NONE;
  let generation = 0;
  const listeners = new Set<() => void>();
  const set = (next: ReasoningEffortState) => { state = next; listeners.forEach((listener) => listener()); };
  const known = (value: ReasoningEffortChoices): ReasoningEffortState => ({ effort: value.effort, efforts: value.efforts, status: "ready" });
  return {
    getSnapshot: () => state,
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    load() {
      if (state.status === "loading" || state.status === "ready" || state.status === "saving") return;
      const mine = ++generation;
      set({ ...state, status: "loading" });
      deps.fetch().then(
        (value) => { if (mine === generation) set(known(value)); },
        () => { if (mine === generation) set({ ...state, status: "error" }); },
      );
    },
    async choose(effort) {
      const previous = state;
      const mine = ++generation;
      set({ ...state, effort, status: "saving" });
      try {
        const saved = await deps.save(effort);
        if (mine === generation) set(known(saved));
      } catch (failure) {
        if (mine === generation) set({ ...previous, status: previous.efforts.length ? "ready" : "error" });
        throw failure;
      }
    },
    reset() { generation++; set(NONE); },
  };
}
