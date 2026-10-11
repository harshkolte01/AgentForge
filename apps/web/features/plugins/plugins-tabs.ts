export type PluginsTab = "plugins" | "skills";

export const pluginsTabs: readonly { id: PluginsTab; label: string }[] = [
  { id: "plugins", label: "Plugins" },
  { id: "skills", label: "Skills" },
];

/** The tab a `?tab=` value names. Anything else (missing, unknown, repeated) opens Plugins. */
export function pluginsTab(value: string | string[] | undefined): PluginsTab {
  return pluginsTabs.find((item) => item.id === value)?.id ?? "plugins";
}

/** How a sign-in with OAuth ended, as the server's callback reported it in the address (`?oauth=connected&plugin=…`, `?oauth=error&code=…`). */
export type OAuthResult = { status: "connected"; plugin: string } | { status: "error"; code: string };

type Query = Record<string, string | string[] | undefined>;
const one = (value: string | string[] | undefined) => (typeof value === "string" ? value : undefined);

/** The result of a sign-in in the page's query, or null. Only what is expected is read: a name of a sensible length, or a plain code. */
export function oauthResult(query: Query): OAuthResult | null {
  const outcome = one(query.oauth);
  if (outcome === "connected") {
    const plugin = one(query.plugin)?.trim();
    return plugin && plugin.length <= 120 ? { status: "connected", plugin } : { status: "connected", plugin: "The plugin" };
  }
  if (outcome === "error") {
    const code = one(query.code);
    return { status: "error", code: code && /^[a-z0-9_]{1,64}$/.test(code) ? code : "plugin_oauth_failed" };
  }
  return null;
}
