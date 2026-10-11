import { SettingsWorkspace, settingsSection } from "@/features/settings/settings-workspace";

export default async function SettingsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const query = await searchParams;
  const agentId = typeof query.agent === "string" ? query.agent : undefined;
  return <SettingsWorkspace section={settingsSection(query.section)} initialCreate={query.create === "1"} agentId={agentId} />;
}
