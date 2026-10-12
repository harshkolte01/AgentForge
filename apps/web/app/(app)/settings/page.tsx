import { redirect } from "next/navigation";
import { SettingsWorkspace, settingsSection } from "@/features/settings/settings-workspace";
import { movedSection } from "@/features/settings/settings-sections";

export default async function SettingsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const query = await searchParams;
  // Old links to the Plugins and Skills sections (bookmarks, an open tab) go to their page, not to Account.
  const moved = movedSection(query.section);
  if (moved) redirect(moved);
  const agentId = typeof query.agent === "string" ? query.agent : undefined;
  return <SettingsWorkspace section={settingsSection(query.section)} initialCreate={query.create === "1"} agentId={agentId} />;
}
