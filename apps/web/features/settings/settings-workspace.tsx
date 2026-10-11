/**
 * Settings entry point. Deliberately not a client module: the server page reads the section from the URL with `settingsSection`,
 * and the client shell renders it.
 */
export { SettingsWorkspace } from "@/features/settings/settings-shell";
export { settingsSection, type SettingsSection } from "@/features/settings/settings-sections";
