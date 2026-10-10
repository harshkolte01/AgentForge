import { SessionBoundary } from "@/components/session-boundary";
import { SettingsWorkspace, type SettingsSection } from "@/features/settings/settings-workspace";

export default async function SettingsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  const query = await searchParams;
  const section: SettingsSection = query.section === "github" || query.section === "agents" || query.section === "runtime" ? query.section : "account";
  const agentId = typeof query.agent === "string" ? query.agent : undefined;
  return <SessionBoundary embedded><SettingsWorkspace section={section} initialCreate={query.create === "1"} agentId={agentId} /></SessionBoundary>;
}
