"use client";
import { useState } from "react";
import { Check, Monitor, Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cardClass, Group, rowClass, sectionClass, SectionHeader, Switch } from "@/features/settings/settings-ui";
import { setEnterSends, setTheme, writeSidebarWidth, type ThemePreference } from "@/lib/preferences";
import { useEnterSends, useThemePreference } from "@/lib/use-preferences";

/** A tiny drawing of each theme (fixed colours, so a tile looks the same whichever theme is on). System shows both halves. */
const themes: { id: ThemePreference; label: string; Icon: typeof Sun; shades: ("light" | "dark")[] }[] = [
  { id: "system", label: "System", Icon: Monitor, shades: ["light", "dark"] },
  { id: "light", label: "Light", Icon: Sun, shades: ["light"] },
  { id: "dark", label: "Dark", Icon: Moon, shades: ["dark"] },
];
const shadeClass = { light: ["bg-[#fafafa]", "bg-[#d4d4d4]"], dark: ["bg-[#171717]", "bg-[#404040]"] } as const;

/** Choices kept in this browser only: they are not part of the account and do not follow you to other devices. */
export function AppearanceSettings() {
  const theme = useThemePreference();
  const enterSends = useEnterSends();
  const [reset, setReset] = useState(false);
  return <section className={sectionClass} aria-labelledby="appearance-title">
    <SectionHeader id="appearance-title" title="Appearance" description="How AgentForge looks and behaves in this browser. These choices are saved on this device only." />
    <fieldset>
      <legend className="mb-3 text-sm font-medium">Theme</legend>
      <div className="grid grid-cols-3 gap-3">
        {themes.map(({ id, label, Icon, shades }) => <label key={id} className="group flex cursor-pointer flex-col gap-2.5 rounded-xl border bg-card p-2.5 transition-colors hover:bg-muted/40 has-[:checked]:border-foreground/50 has-[:checked]:ring-1 has-[:checked]:ring-foreground/20 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring/60 motion-reduce:transition-none">
          <input type="radio" name="theme" value={id} checked={theme === id} onChange={() => setTheme(id)} className="sr-only" />
          <span aria-hidden="true" className="flex h-16 overflow-hidden rounded-lg border sm:h-20">{shades.map((shade) => <span key={shade} className={`flex min-w-0 flex-1 flex-col justify-end gap-1.5 p-2.5 ${shadeClass[shade][0]}`}><span className={`h-1.5 w-3/5 rounded-full ${shadeClass[shade][1]}`} /><span className={`h-1.5 w-2/5 rounded-full ${shadeClass[shade][1]}`} /></span>)}</span>
          <span className="flex items-center gap-2 px-0.5 pb-0.5 text-sm font-medium"><Icon className="size-4 text-muted-foreground" aria-hidden="true" />{label}<span aria-hidden="true" className="ml-auto grid size-4 place-items-center rounded-full bg-foreground text-background opacity-0 transition-opacity group-has-[:checked]:opacity-100 motion-reduce:transition-none"><Check className="size-2.5" strokeWidth={3.5} /></span></span>
        </label>)}
      </div>
      <p className="mt-3 text-[0.8125rem] leading-5 text-muted-foreground">System follows your device’s light or dark setting.</p>
    </fieldset>
    <Group title="Composer">
      <div className={`${cardClass} ${rowClass}`}>
        <div className="min-w-0 flex-1 space-y-0.5"><p className="text-sm font-medium">Enter to send</p><p id="enter-sends-hint" className="text-[0.8125rem] leading-5 text-muted-foreground">{enterSends ? "Enter sends your message; Shift+Enter adds a new line." : "Enter adds a new line; ⌘+Enter or Ctrl+Enter sends."}</p></div>
        <Switch checked={enterSends} onChange={setEnterSends} label="Enter to send" describedBy="enter-sends-hint" />
      </div>
    </Group>
    <Group title="Sidebar">
      <div className={`${cardClass} ${rowClass}`}>
        <div className="min-w-0 flex-1 space-y-0.5"><p className="text-sm font-medium">Sidebar width</p><p role="status" className="text-[0.8125rem] leading-5 text-muted-foreground">{reset ? "The sidebar is back to its default width." : "Drag the sidebar edge to resize it. Reset it to the default here."}</p></div>
        <Button variant="outline" onClick={() => { writeSidebarWidth(null); setReset(true); }}>Reset sidebar width</Button>
      </div>
    </Group>
  </section>;
}
