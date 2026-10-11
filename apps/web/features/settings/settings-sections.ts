export type SettingsSection = "account" | "agent" | "status" | "github" | "plugins" | "conversations" | "appearance" | "agents";

export const settingsSections: readonly { id: SettingsSection; label: string; labs?: boolean }[] = [
  { id: "account", label: "Account" },
  { id: "agent", label: "Agent" },
  { id: "status", label: "Status" },
  { id: "github", label: "GitHub" },
  { id: "plugins", label: "Plugins" },
  { id: "conversations", label: "Conversations" },
  { id: "appearance", label: "Appearance" },
  { id: "agents", label: "Custom agents", labs: true },
];

/** The section a `?section=` value names. Old links keep working: "runtime" was the section now called Status. */
export function settingsSection(value: string | string[] | undefined): SettingsSection {
  if (value === "runtime") return "status";
  return settingsSections.find((item) => item.id === value)?.id ?? "account";
}
