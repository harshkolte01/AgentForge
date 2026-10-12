"use client";
import { useEffect, useSyncExternalStore } from "react";
import { fetchPlugins } from "@/lib/plugins-api";
import { createPluginsStore, type PluginsState } from "@/lib/plugins-store";
import { message } from "@/lib/task-api";

/** One copy for the page: Settings and every composer share it. */
export const pluginsStore = createPluginsStore({ fetch: () => fetchPlugins(), describe: message });
const idle: PluginsState = { status: "idle", overview: null, error: "" };
if (typeof window !== "undefined") for (const event of ["agentforge-session-expired", "agentforge-account-changed"]) window.addEventListener(event, () => pluginsStore.reset());

/** The person's plugins, read on first use. */
export function usePlugins() {
  const state = useSyncExternalStore(pluginsStore.subscribe, pluginsStore.getSnapshot, () => idle);
  useEffect(() => { pluginsStore.load(); }, []);
  return state;
}
