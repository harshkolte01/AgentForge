import { routes } from "@/lib/routes";

export type SettingsSection = "account" | "agent" | "github" | "conversations" | "appearance" | "agents";

export const settingsSections: readonly { id: SettingsSection; label: string; labs?: boolean }[] = [
  { id: "account", label: "Account" },
  { id: "agent", label: "Agent" },
  { id: "github", label: "GitHub" },
  { id: "conversations", label: "Conversations" },
  { id: "appearance", label: "Appearance" },
  { id: "agents", label: "Custom agents", labs: true },
];

/** Plugins and skills moved out of Settings to the Plugins page in the sidebar. Where an old `?section=` link for either now goes. */
export function movedSection(value: string | string[] | undefined): string | null {
  return value === "plugins" ? routes.plugins() : value === "skills" ? routes.plugins("skills") : null;
}

/** Retired sections and unknown values safely fall back to Account. */
export function settingsSection(value: string | string[] | undefined): SettingsSection {
  return settingsSections.find((item) => item.id === value)?.id ?? "account";
}
