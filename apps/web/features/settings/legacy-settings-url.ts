type LegacyQuery = Record<string, string | string[] | undefined>;

export function legacySettingsUrl(section: "github" | "agents", query: LegacyQuery, create = false) {
  const destination = new URLSearchParams({ section });
  if (section === "agents" && typeof query.agent === "string") destination.set("agent", query.agent);
  if (create || query.create === "1") destination.set("create", "1");
  return `/settings?${destination}`;
}
