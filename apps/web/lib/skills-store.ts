/**
 * The person's skills, read once per page session and shared by Settings → Skills and every composer's skill menu, so a skill added
 * or switched off in Settings shows in the chat at once. A change returns the changed skill, which replaces its old copy here.
 */
import type { Skill, SkillsOverview } from "@/lib/skills-api";

export type SkillsState = { status: "idle" | "loading" | "ready" | "error"; overview: SkillsOverview | null; error: string };
export type SkillsStore = {
  getSnapshot(): SkillsState;
  subscribe(listener: () => void): () => void;
  /** Reads the overview unless it is known or being read. */
  load(): void;
  /** Reads it again whatever is known; the old copy stays on screen meanwhile. */
  reload(): void;
  /** Puts a skill the server returned in place (added when new). A detail's instructions are not kept: the list never carries them. */
  apply(skill: Skill): void;
  remove(key: string): void;
  reset(): void;
};

export function createSkillsStore(deps: { fetch: () => Promise<SkillsOverview>; describe: (failure: unknown) => string }): SkillsStore {
  let state: SkillsState = { status: "idle", overview: null, error: "" };
  let generation = 0;
  const listeners = new Set<() => void>();
  const set = (next: SkillsState) => { state = next; listeners.forEach((listener) => listener()); };
  function read() {
    const mine = ++generation;
    set({ ...state, status: "loading", error: "" });
    deps.fetch().then(
      (overview) => { if (mine === generation) set({ status: "ready", overview, error: "" }); },
      (failure) => { if (mine === generation) set({ ...state, status: "error", error: deps.describe(failure) }); },
    );
  }
  function change(update: (overview: SkillsOverview) => SkillsOverview) {
    if (!state.overview) return;
    generation++; // a read that began before this change would bring the old list back
    set({ status: "ready", overview: update(state.overview), error: "" });
  }
  const summary = (skill: Skill): Skill => { const rest = { ...skill }; delete rest.instructions; return rest; };
  return {
    getSnapshot: () => state,
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    load() { if (state.status === "idle" || state.status === "error") read(); },
    reload() { read(); },
    apply(skill) {
      const shown = summary(skill);
      change((overview) => {
        const group = skill.source === "builtin" ? "builtin" : "custom";
        const exists = overview[group].some((item) => item.key === skill.key);
        return { ...overview, [group]: exists ? overview[group].map((item) => (item.key === skill.key ? shown : item)) : [...overview[group], shown] };
      });
    },
    remove(key) { change((overview) => ({ ...overview, custom: overview.custom.filter((item) => item.key !== key) })); },
    reset() { generation++; set({ status: "idle", overview: null, error: "" }); },
  };
}
