import { api } from "@/lib/task-api";

/** Public HTTP shapes of `apps/api/src/agentforge/api/routes_skills.py`. */
export type Skill = {
  /** `builtin:<name>` or `custom:<id>`. */
  key: string;
  source: "builtin" | "custom";
  name: string;
  description: string;
  /** Switched on: the agent is told about it and can load it. */
  enabled: boolean;
  updated_at: string | null;
  /** Only in a single skill's detail. */
  instructions?: string;
};
export type SkillsOverview = { enabled: boolean; max_custom: number; builtin: Skill[]; custom: Skill[] };
export type SkillFields = { name: string; description: string; instructions: string };

const path = (key: string) => `/skills/${encodeURIComponent(key)}`;

export const fetchSkills = (init: RequestInit = {}) => api<SkillsOverview>("/skills", init);
export const fetchSkill = (key: string) => api<Skill>(path(key));
export const createSkill = (fields: SkillFields & { enabled?: boolean }) => api<Skill>("/skills", { method: "POST", body: JSON.stringify(fields) });
export const changeSkill = (key: string, patch: Partial<SkillFields> & { enabled?: boolean }) => api<Skill>(path(key), { method: "PATCH", body: JSON.stringify(patch) });
export const removeSkill = (key: string) => api<void>(path(key), { method: "DELETE" });

// The limits the server enforces (apps/api/src/agentforge/skills/format.py), repeated here only to guide typing.
export const NAME_MIN = 3;
export const NAME_MAX = 40;
export const DESCRIPTION_MAX = 300;
export const INSTRUCTIONS_MAX = 10_000;
const NAME_PATTERN = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;

/** What is wrong with a skill name as typed, in words, or null when it is acceptable. */
export function nameProblem(name: string): string | null {
  const value = name.trim();
  if (value.length < NAME_MIN || value.length > NAME_MAX || !NAME_PATTERN.test(value)) {
    return `Use ${NAME_MIN}-${NAME_MAX} characters: lowercase letters, digits and single hyphens, starting with a letter.`;
  }
  return null;
}

export type ParsedSkill = { name?: string; description?: string; instructions: string };

/**
 * The fields in a `SKILL.md`: front matter (`name:` and `description:` between lines of three dashes), then the instructions. Lenient on
 * purpose: a file with no front matter is all instructions, and the server judges whatever the person ends up saving.
 */
export function parseSkillMarkdown(source: string): ParsedSkill {
  const lines = source.replace(/^﻿/, "").replace(/\r\n?/g, "\n").split("\n");
  if (lines[0]?.trim() !== "---") return { instructions: lines.join("\n").trim() };
  const end = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  if (end < 0) return { instructions: lines.join("\n").trim() };
  const fields: Record<string, string> = {};
  for (const line of lines.slice(1, end)) {
    const split = line.indexOf(":");
    if (split <= 0 || line.trimStart().startsWith("#")) continue;
    let value = line.slice(split + 1).trim();
    if (value.length >= 2 && value[0] === value[value.length - 1] && (value[0] === "\"" || value[0] === "'")) value = value.slice(1, -1);
    fields[line.slice(0, split).trim().toLowerCase()] = value;
  }
  return { name: fields.name, description: fields.description, instructions: lines.slice(end + 1).join("\n").trim() };
}

/** A skill as a `SKILL.md`, which `parseSkillMarkdown` and the server's importer read back. */
export function formatSkillMarkdown(skill: SkillFields): string {
  return `---\nname: ${skill.name}\ndescription: ${skill.description.replace(/\s+/g, " ").trim()}\n---\n${skill.instructions.trim()}\n`;
}

/** `goal` with its leading `/skill` (if any) replaced by `/name`, so choosing a skill never stacks two. */
export function withSkill(goal: string, name: string): string {
  return `/${name} ${goal.replace(/^\s*\/[a-z0-9-]*[ \t]*/i, "")}`.slice(0, 8000);
}

/** The name for a copy of `name`, which must not equal it (built-in names are reserved) and must fit the limit. */
export function copyName(name: string): string {
  return `${name.slice(0, NAME_MAX - "-custom".length).replace(/-+$/, "")}-custom`;
}

/** Skills that are switched on, for the composer's menu: the person's own first, then the built-in ones. */
export function usable(overview: SkillsOverview | null): Skill[] {
  return overview?.enabled ? [...overview.custom, ...overview.builtin].filter((skill) => skill.enabled) : [];
}
