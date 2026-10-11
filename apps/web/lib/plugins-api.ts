import { api } from "@/lib/task-api";

/** Public HTTP shapes of `apps/api/src/agentforge/api/routes_plugins.py`. A credential is never sent to the browser: only whether one is saved. */
export type PluginTool = { name: string; title: string; description: string; read_only: boolean; enabled: boolean };
export type PluginConnection = {
  id: string; plugin_key: string; name: string; server_url: string; description: string | null;
  auth: "none" | "token"; has_credential: boolean;
  /** Switched on for new conversations. */
  enabled: boolean;
  status: "connected" | "error"; last_error: string | null; server_name: string | null;
  tools: PluginTool[]; tool_count: number; enabled_tool_count: number; tools_synced_at: string | null; created_at: string | null;
};
export type PluginCatalogEntry = {
  key: string; name: string; description: string; server_url: string; auth: "none" | "optional" | "required";
  category: string; token_label: string; token_help: string; docs_url: string | null;
};
export type PluginsOverview = {
  catalog: PluginCatalogEntry[]; connections: PluginConnection[];
  /** Plugins are turned on for this server. */
  enabled: boolean;
  /** The server can save credentials (an encryption key is configured). */
  credentials: boolean; max_connections: number;
};
export type PluginConnectInput = { plugin_key: string; name?: string; server_url?: string; token?: string; header_name?: string };
export type PluginChange = { enabled?: boolean; token?: string; disabled_tools?: string[] };

const path = (id: string) => `/plugins/${encodeURIComponent(id)}`;

export const fetchPlugins = (init: RequestInit = {}) => api<PluginsOverview>("/plugins", init);
export const connectPlugin = (input: PluginConnectInput) => api<PluginConnection>("/plugins", { method: "POST", body: JSON.stringify(input) });
export const changePlugin = (id: string, change: PluginChange) => api<PluginConnection>(path(id), { method: "PATCH", body: JSON.stringify(change) });
export const refreshPlugin = (id: string) => api<PluginConnection>(`${path(id)}/refresh`, { method: "POST" });
export const removePlugin = (id: string) => api<void>(path(id), { method: "DELETE" });

/** The plugins a new conversation starts with: the connections left switched on. */
export function defaultSelection(connections: readonly PluginConnection[]): string[] {
  return connections.filter((connection) => connection.enabled).map((connection) => connection.id);
}

/**
 * The plugins a conversation uses, as shown and sent. `chosen` is the conversation's own list (null for a conversation not created
 * yet whose person has not chosen: it will start with the defaults). Plugins that were removed drop out.
 */
export function effectiveSelection(connections: readonly PluginConnection[], chosen: readonly string[] | null | undefined): string[] {
  if (!chosen) return defaultSelection(connections);
  const known = new Set(connections.map((connection) => connection.id));
  return chosen.filter((id) => known.has(id));
}

/** `id` switched on or off in `selection`, keeping the order the plugins were connected in. */
export function toggled(connections: readonly PluginConnection[], selection: readonly string[], id: string, on: boolean): string[] {
  const next = new Set(selection);
  if (on) next.add(id); else next.delete(id);
  return connections.filter((connection) => next.has(connection.id)).map((connection) => connection.id);
}

/** "mcp.linear.app" for a server address: where a plugin lives, without its path or query. */
export function serverHost(url: string): string {
  try { return new URL(url).host; } catch { return url; }
}

/** How many of a connection's tools only read and how many may change something, in words. */
export function toolSummary(connection: Pick<PluginConnection, "tools" | "tool_count" | "enabled_tool_count">): string {
  const count = (value: number) => `${value} ${value === 1 ? "tool" : "tools"}`;
  const off = connection.tool_count - connection.enabled_tool_count;
  const base = off > 0 ? `${connection.enabled_tool_count} of ${count(connection.tool_count)} on` : count(connection.tool_count);
  const changing = connection.tools.filter((tool) => tool.enabled && !tool.read_only).length;
  return changing ? `${base} · ${changing} can change things` : base;
}

/** More tools than this in one plugin make every request of a conversation heavier; Settings suggests switching some off. */
export const MANY_TOOLS = 40;
