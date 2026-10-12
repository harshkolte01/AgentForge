/**
 * The first-run setup list on the empty chat: what this workspace still needs, from `GET /api/v1/configuration`. Required items
 * block sending (no model) or leave messages waiting (no worker); optional ones only unlock more (web research, the coding
 * sandbox, GitHub). Every field added by newer servers is read defensively: an unknown value is never reported as missing.
 */
import type { WorkspaceConfiguration } from "@/lib/workspace-api";

export type SetupItem = { id: "model" | "worker" | "web" | "sandbox" | "github"; label: string; detail: string; required: boolean; href?: string };
/** Whether the GitHub App can be connected and is: null when unknown (not asked, or an older server). */
export type GitHubSetup = { configured: boolean; connected: boolean } | null;

const settingsList = (names: readonly string[] | undefined) => names?.length ? names.join(", ") : "";

export function modelConfigured(config: WorkspaceConfiguration): boolean {
  return !!(config.general_configured || config.research_configured || config.coding_configured);
}

/** The setup steps still to do, required first. Empty when nothing is missing. */
export function setupItems(config: WorkspaceConfiguration, github: GitHubSetup = null): SetupItem[] {
  const items: SetupItem[] = [];
  if (!modelConfigured(config)) {
    const missing = settingsList(config.general_missing_settings ?? config.missing_settings);
    items.push({ id: "model", label: "Connect a model", detail: missing ? `Set ${missing} in the API's .env, then restart it.` : "Set the LLM_* settings in the API's .env, then restart it.", required: true, href: "/settings?section=agent" });
  }
  if (config.worker?.status === "offline") items.push({ id: "worker", label: "Start the worker", detail: "Messages wait until a worker is running: scripts/dev.sh starts it with the API.", required: true });
  if (config.web_research_configured === false) items.push({ id: "web", label: "Turn on web research", detail: "Set EXA_API_KEY (or TAVILY_API_KEY) to let the agent search the web.", required: false });
  if (config.coding_configured === false) {
    const missing = settingsList(config.coding_missing_settings);
    items.push({ id: "sandbox", label: "Turn on the coding sandbox", detail: missing ? `Set ${missing} to let the agent run code.` : "Set E2B_API_KEY to let the agent run code.", required: false });
  }
  if (github?.configured && !github.connected) items.push({ id: "github", label: "Connect GitHub", detail: "Work on your own repositories and open pull requests.", required: false, href: "/settings?section=github" });
  return items;
}

/**
 * How the list shows: "open" while something required is missing, "collapsed" (one quiet line that opens the list) when only
 * optional items remain, "hidden" when nothing is left or the person dismissed it.
 */
export function checklistMode(items: readonly SetupItem[], dismissed: boolean): "open" | "collapsed" | "hidden" {
  if (dismissed || !items.length) return "hidden";
  return items.some((item) => item.required) ? "open" : "collapsed";
}
