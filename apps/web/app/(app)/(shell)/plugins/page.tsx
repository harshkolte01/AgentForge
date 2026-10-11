import { PluginsPage } from "@/features/plugins/plugins-page";
import { oauthResult, pluginsTab } from "@/features/plugins/plugins-tabs";

/**
 * Plugins and skills, drawn inside the workspace shell in `../layout.tsx`. `?tab=skills` opens the Skills tab; `?oauth=…` is where the
 * server's sign-in callback sends the browser back to, saying how a sign-in to a plugin went.
 */
export default async function PluginsRoute({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const query = await searchParams;
  return <PluginsPage tab={pluginsTab(query.tab)} result={oauthResult(query)} />;
}
