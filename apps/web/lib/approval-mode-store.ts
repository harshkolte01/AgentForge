/**
 * The person's approval mode, read once per page session and shared by every control that shows it (the composer's picker in
 * each conversation, Settings → Agent), so moving between conversations never refetches it and a change shows everywhere at once.
 * A failed read falls back to "ask" (the server's default) and is retried by the next `load()`.
 */
import type { ApprovalMode } from "@/lib/activity/approvals";

export type ApprovalModeState = { mode: ApprovalMode | null; status: "idle" | "loading" | "ready" | "error" | "saving" };
export type ApprovalModeStore = {
  getSnapshot(): ApprovalModeState;
  subscribe(listener: () => void): () => void;
  /** Reads the mode unless it is already known or being read. */
  load(): void;
  /** Saves a new mode, showing it at once; a failure puts the previous mode back and rejects. */
  choose(mode: ApprovalMode): Promise<void>;
  /** Forgets the mode (another account signed in). */
  reset(): void;
};

export function createApprovalModeStore(deps: { fetch: () => Promise<{ mode: ApprovalMode }>; save: (mode: ApprovalMode) => Promise<{ mode: ApprovalMode }> }): ApprovalModeStore {
  let state: ApprovalModeState = { mode: null, status: "idle" };
  let generation = 0;
  const listeners = new Set<() => void>();
  const set = (next: ApprovalModeState) => { state = next; listeners.forEach((listener) => listener()); };
  return {
    getSnapshot: () => state,
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    load() {
      if (state.status === "loading" || state.status === "ready" || state.status === "saving") return;
      const mine = ++generation;
      set({ mode: state.mode, status: "loading" });
      deps.fetch().then(
        (value) => { if (mine === generation) set({ mode: value.mode, status: "ready" }); },
        () => { if (mine === generation) set({ mode: state.mode ?? "ask", status: "error" }); },
      );
    },
    async choose(mode) {
      const previous = state.mode;
      const mine = ++generation;
      set({ mode, status: "saving" });
      try {
        const saved = await deps.save(mode);
        if (mine === generation) set({ mode: saved.mode, status: "ready" });
      } catch (failure) {
        if (mine === generation) set({ mode: previous, status: previous ? "ready" : "error" });
        throw failure;
      }
    },
    reset() { generation++; set({ mode: null, status: "idle" }); },
  };
}
