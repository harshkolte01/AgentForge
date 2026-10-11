export type SettingsSection = "account" | "agent" | "github" | "plugins" | "skills" | "conversations" | "appearance" | "agents";

export const settingsSections: readonly { id: SettingsSection; label: string; labs?: boolean }[] = [
  { id: "account", label: "Account" },
  { id: "agent", label: "Agent" },
  { id: "github", label: "GitHub" },
  { id: "plugins", label: "Plugins" },
  { id: "skills", label: "Skills" },
  { id: "conversations", label: "Conversations" },
  { id: "appearance", label: "Appearance" },
  { id: "agents", label: "Custom agents", labs: true },
];

/** Retired sections and unknown values safely fall back to Account. */
export function settingsSection(value: string | string[] | undefined): SettingsSection {
  return settingsSections.find((item) => item.id === value)?.id ?? "account";
}
