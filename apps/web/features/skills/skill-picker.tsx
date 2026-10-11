"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Sparkles } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { chipClass, optionClass } from "@/features/sessions/composer-styles";
import { skillsStore, useSkills } from "@/features/skills/use-skills";
import { usable } from "@/lib/skills-api";
import { routes } from "@/lib/routes";

const linkClass = "rounded underline underline-offset-4 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60";

/**
 * The skills that are switched on, to start a message with: choosing one puts `/name ` at the front of the message. The agent loads a
 * skill on its own when a request fits its description; this is how a person asks for one by name. Skills are managed in Settings.
 */
export function SkillPicker({ onPick, disabled, side = "top" }: { onPick: (name: string) => void; disabled: boolean; side?: "top" | "bottom" }) {
  const { status, overview, error } = useSkills();
  const [open, setOpen] = useState(false);
  // After a choice the message field has the caret (the composer put it there), so closing the menu must not take focus back to its button.
  const chose = useRef(false);
  // Opening the menu reads the list again, so a skill changed in another tab is there; the old list stays on screen meanwhile.
  useEffect(() => { if (open) chose.current = false; if (open && status !== "loading") skillsStore.reload(); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  if (overview && !overview.enabled) return null;
  const skills = usable(overview);
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger disabled={disabled} aria-label="Skills" title="Start with a skill" className={`${chipClass} justify-center max-sm:w-11 max-sm:px-0`}>
      <Sparkles className="size-4" aria-hidden="true" />
      <span className="hidden sm:inline">Skills</span>
    </PopoverTrigger>
    <PopoverContent side={side} align="start" finalFocus={() => !chose.current} className="w-[min(21rem,calc(100vw-1.5rem))] rounded-xl shadow-sm">
      <div className="p-1" role="group" aria-label="Skills">
        <p className="px-2.5 pb-1 pt-2 text-xs leading-5 text-muted-foreground">The agent picks a skill when your request fits. Choose one to ask for it by name.</p>
        {!overview && status !== "error" ? <div role="status" className="space-y-2 px-2.5 py-2"><span className="sr-only">Loading skills…</span><div className="h-4 w-40 animate-pulse rounded bg-muted motion-reduce:animate-none" /><div className="h-4 w-28 animate-pulse rounded bg-muted motion-reduce:animate-none" /></div> : null}
        {status === "error" && !overview ? <p role="alert" className="px-2.5 py-2 text-xs leading-5 text-destructive">{error} <button type="button" className={linkClass} onClick={() => skillsStore.reload()}>Try again</button></p> : null}
        {overview && !skills.length ? <p className="px-2.5 py-2 text-[13px] leading-5">No skills are switched on.</p> : null}
        <div className="max-h-72 overflow-y-auto overscroll-contain">
          {skills.map((skill) => <button key={skill.key} type="button" className={`${optionClass} items-start`} onClick={() => { chose.current = true; setOpen(false); onPick(skill.name); }}>
            <span className="min-w-0 flex-1">
              <span className="block truncate font-mono text-[12.5px]">/{skill.name}{skill.source === "custom" ? <span className="ml-1.5 font-sans text-[11px] text-muted-foreground">yours</span> : null}</span>
              <span className="line-clamp-2 block text-xs leading-4 text-muted-foreground">{skill.description}</span>
            </span>
          </button>)}
        </div>
        <div className="mt-1 border-t px-2.5 py-2 text-xs leading-5 text-muted-foreground"><Link href={routes.settings("skills")} className={linkClass}>Manage skills</Link></div>
      </div>
    </PopoverContent>
  </Popover>;
}
