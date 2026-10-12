"use client";
import { useEffect, useSyncExternalStore } from "react";
import { fetchSkills } from "@/lib/skills-api";
import { createSkillsStore, type SkillsState } from "@/lib/skills-store";
import { message } from "@/lib/task-api";

/** One copy for the page: Settings and every composer share it. */
export const skillsStore = createSkillsStore({ fetch: () => fetchSkills(), describe: message });
const idle: SkillsState = { status: "idle", overview: null, error: "" };
if (typeof window !== "undefined") for (const event of ["agentforge-session-expired", "agentforge-account-changed"]) window.addEventListener(event, () => skillsStore.reset());

/** The person's skills, read on first use. */
export function useSkills() {
  const state = useSyncExternalStore(skillsStore.subscribe, skillsStore.getSnapshot, () => idle);
  useEffect(() => { skillsStore.load(); }, []);
  return state;
}
