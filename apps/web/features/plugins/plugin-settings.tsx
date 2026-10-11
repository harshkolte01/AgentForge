"use client";
import { PluginsPanel } from "@/features/plugins/plugins-panel";
import { sectionClass, SectionHeader } from "@/features/settings/settings-ui";

/** Settings → Plugins: the same panel as the Plugins page, under a section header. */
export function PluginSettings() {
  return <section className={sectionClass} aria-labelledby="plugins-title">
    <SectionHeader id="plugins-title" title="Plugins" description="Connect services so the agent can use their tools. The ones switched on here are available in every new conversation." />
    <PluginsPanel />
  </section>;
}
