/**
 * The person's plugins (the catalog and their connections), read once per page session and shared by the Plugins page and
 * Settings → Plugins, so moving between them never refetches it. A change returns the changed connection, which replaces its old
 * copy here. (A new conversation's plugins are chosen by the server from the ones left on: the composer does not read this.)
 */
import type { PluginConnection, PluginsOverview } from "@/lib/plugins-api";

export type PluginsState = { status: "idle" | "loading" | "ready" | "error"; overview: PluginsOverview | null; error: string };
export type PluginsStore = {
  getSnapshot(): PluginsState;
  subscribe(listener: () => void): () => void;
  /** Reads the overview unless it is known or being read. */
  load(): void;
  /** Reads it again whatever is known (Refresh, back from signing in to a plugin, a failed change). The old copy stays on screen meanwhile. */
  reload(): void;
  /** Puts a connection the server returned in place (added when new). */
  apply(connection: PluginConnection): void;
  remove(id: string): void;
  /** Forgets everything (another account signed in). */
  reset(): void;
};

export function createPluginsStore(deps: { fetch: () => Promise<PluginsOverview>; describe: (failure: unknown) => string }): PluginsStore {
  let state: PluginsState = { status: "idle", overview: null, error: "" };
  let generation = 0;
  const listeners = new Set<() => void>();
  const set = (next: PluginsState) => { state = next; listeners.forEach((listener) => listener()); };
  function read() {
    const mine = ++generation;
    set({ ...state, status: "loading", error: "" });
    deps.fetch().then(
      (overview) => { if (mine === generation) set({ status: "ready", overview, error: "" }); },
      (failure) => { if (mine === generation) set({ ...state, status: "error", error: deps.describe(failure) }); },
    );
  }
  function change(update: (overview: PluginsOverview) => PluginsOverview) {
    // A read in flight started before this change and would bring the old copy back.
    if (!state.overview) return;
    generation++;
    set({ status: "ready", overview: update(state.overview), error: "" });
  }
  return {
    getSnapshot: () => state,
    subscribe(listener) { listeners.add(listener); return () => { listeners.delete(listener); }; },
    load() { if (state.status === "idle" || state.status === "error") read(); },
    reload() { read(); },
    apply(connection) {
      change((overview) => {
        const exists = overview.connections.some((item) => item.id === connection.id);
        return { ...overview, connections: exists ? overview.connections.map((item) => (item.id === connection.id ? connection : item)) : [...overview.connections, connection] };
      });
    },
    remove(id) { change((overview) => ({ ...overview, connections: overview.connections.filter((item) => item.id !== id) })); },
    reset() { generation++; set({ status: "idle", overview: null, error: "" }); },
  };
}
