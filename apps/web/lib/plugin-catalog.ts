import type { PluginCatalogEntry } from "@/lib/plugins-api";

/** The order the catalog's categories are shown in; one the server adds later goes after these, alphabetically. */
export const categoryOrder = ["Code", "Docs", "Cloud", "Data", "Observability", "Planning", "Design", "Business", "Search"] as const;

const rank = (category: string) => { const index = (categoryOrder as readonly string[]).indexOf(category); return index < 0 ? categoryOrder.length : index; };

/** The categories present in `entries`, in display order. */
export function categoriesOf(entries: readonly PluginCatalogEntry[]): string[] {
  return [...new Set(entries.map((entry) => entry.category))].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

/** `entries` in groups by category, in display order; each group keeps the catalog's own order. */
export function groupByCategory(entries: readonly PluginCatalogEntry[]): { category: string; entries: PluginCatalogEntry[] }[] {
  return categoriesOf(entries).map((category) => ({ category, entries: entries.filter((entry) => entry.category === category) }));
}

/** Whether an entry matches what was typed in the search box (name, category or description), ignoring case and spacing. */
export function matchesEntry(entry: Pick<PluginCatalogEntry, "name" | "description" | "category" | "key">, query: string): boolean {
  const needle = query.trim().toLowerCase().replace(/\s+/g, " ");
  if (!needle) return true;
  return [entry.name, entry.key, entry.category, entry.description].some((text) => text.toLowerCase().includes(needle));
}

export type SignIn = "oauth" | "oauth-or-token" | "token" | "token-optional" | "open";

/** How a person signs in to a catalog plugin, from what the catalog says. */
export function signInOf(entry: Pick<PluginCatalogEntry, "auth" | "oauth">): SignIn {
  if (entry.oauth) return entry.auth === "optional" ? "oauth-or-token" : "oauth";
  return entry.auth === "required" ? "token" : entry.auth === "optional" ? "token-optional" : "open";
}

/** "an API key", "a personal access token": a token's name in a sentence, with the right article and acronyms left as they are. */
function withArticle(label: string): string {
  const [first = ""] = label.split(" ");
  const phrase = first.length > 1 && first === first.toUpperCase() ? label : label.charAt(0).toLowerCase() + label.slice(1);
  return `${/^[aeiou]/i.test(phrase) ? "an" : "a"} ${phrase}`;
}

/** A few words saying how a plugin signs in, for the connect dialog. */
export function signInLabel(entry: Pick<PluginCatalogEntry, "auth" | "oauth" | "token_label">): string {
  switch (signInOf(entry)) {
    case "oauth": return "Sign in with your account";
    case "oauth-or-token": return `Sign in, or use ${withArticle(entry.token_label)}`;
    case "token": return `Needs ${withArticle(entry.token_label)}`;
    case "token-optional": return `Works without ${withArticle(entry.token_label)}`;
    default: return "No account needed";
  }
}

/** "Use an API key instead": the link that switches a plugin that can sign in over to a pasted token. */
export function tokenInstead(entry: Pick<PluginCatalogEntry, "token_label">): string {
  return `Use ${withArticle(entry.token_label)} instead`;
}
