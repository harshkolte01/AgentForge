"use client";
import { useState } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { choiceClass, radioClass, sectionClass, SectionHeader, subheadingClass, subsectionClass } from "@/features/settings/settings-ui";
import { setEnterSends, setTheme, writeSidebarWidth, type ThemePreference } from "@/lib/preferences";
import { useEnterSends, useThemePreference } from "@/lib/use-preferences";

const themes: { id: ThemePreference; label: string; Icon: typeof Sun }[] = [
  { id: "system", label: "System", Icon: Monitor },
  { id: "light", label: "Light", Icon: Sun },
  { id: "dark", label: "Dark", Icon: Moon },
];

/** Choices kept in this browser only: they are not part of the account and do not follow you to other devices. */
export function AppearanceSettings() {
  const theme = useThemePreference();
  const enterSends = useEnterSends();
  const [reset, setReset] = useState(false);
  return <section className={sectionClass} aria-labelledby="appearance-title">
    <SectionHeader id="appearance-title" title="Appearance" description="How AgentForge looks and behaves in this browser. These choices are saved on this device only." />
    <fieldset>
      <legend className={`${subheadingClass} mb-3`}>Theme</legend>
      <div className="grid gap-2 sm:grid-cols-3">
        {themes.map(({ id, label, Icon }) => <label key={id} className={`${choiceClass} items-center`}>
          <input type="radio" name="theme" value={id} checked={theme === id} onChange={() => setTheme(id)} className={radioClass} />
          <Icon className="size-4 text-muted-foreground" aria-hidden="true" /><span className="text-sm font-medium">{label}</span>
        </label>)}
      </div>
      <p className="mt-3 text-xs leading-5 text-muted-foreground">System follows your device’s light or dark setting.</p>
    </fieldset>
    <div className={subsectionClass}>
      <h3 className={subheadingClass}>Composer</h3>
      <label className="flex cursor-pointer items-start gap-3">
        <input type="checkbox" checked={enterSends} onChange={(event) => setEnterSends(event.target.checked)} className="mt-0.5 size-4 shrink-0 accent-primary" aria-describedby="enter-sends-hint" />
        <span className="space-y-1"><span className="block text-sm font-medium">Enter to send</span><span id="enter-sends-hint" className="block text-sm leading-6 text-muted-foreground">{enterSends ? "Enter sends your message; Shift+Enter adds a new line." : "Enter adds a new line; ⌘+Enter or Ctrl+Enter sends."}</span></span>
      </label>
    </div>
    <div className={subsectionClass}>
      <h3 className={subheadingClass}>Sidebar</h3>
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" onClick={() => { writeSidebarWidth(null); setReset(true); }}>Reset sidebar width</Button>
        <p role="status" className="text-sm text-muted-foreground">{reset ? "The sidebar is back to its default width." : ""}</p>
      </div>
    </div>
  </section>;
}
