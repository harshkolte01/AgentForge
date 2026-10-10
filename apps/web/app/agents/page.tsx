import { redirect } from "next/navigation";
import { legacySettingsUrl } from "@/features/settings/legacy-settings-url";
export default async function AgentsPage({ searchParams }: { searchParams: Promise<Record<string, string | string[] | undefined>> }) {
  redirect(legacySettingsUrl("agents", await searchParams));
}
