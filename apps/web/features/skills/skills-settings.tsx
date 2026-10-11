"use client";
import { sectionClass, SectionHeader } from "@/features/settings/settings-ui";
import { SkillsPanel } from "@/features/skills/skills-panel";

/** Settings → Skills: the same panel as the Plugins page, under a section header. */
export function SkillsSettings() {
  return <section className={sectionClass} aria-labelledby="skills-title">
    <SectionHeader id="skills-title" title="Skills" description={<>Skills are playbooks the agent follows for a kind of work. It loads one when your request fits its description, or when you start a message with <code className="rounded bg-muted px-1 font-mono text-[0.85em]">/skill-name</code>. Add your own, or switch off the built-in ones you do not want.</>} />
    <SkillsPanel />
  </section>;
}
