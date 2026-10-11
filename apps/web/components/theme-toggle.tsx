"use client";
import { useRef, type KeyboardEvent } from "react";
import { Monitor, Moon, Sun } from "lucide-react";
import { setTheme, type ThemePreference } from "@/lib/preferences";
import { useThemePreference } from "@/lib/use-preferences";

const options: { id: ThemePreference; label: string; Icon: typeof Sun }[] = [
  { id: "system", label: "System", Icon: Monitor },
  { id: "light", label: "Light", Icon: Sun },
  { id: "dark", label: "Dark", Icon: Moon },
];

/**
 * System / Light / Dark, the same browser-local choice as Settings → Appearance (one store, so changing it here changes it
 * everywhere, in every open tab, with no flash). A radio group: one stop in the tab order, arrow keys move and select.
 */
export function ThemeToggle({ className = "" }: { className?: string }) {
  const theme = useThemePreference();
  const group = useRef<HTMLDivElement>(null);
  function keys(event: KeyboardEvent<HTMLDivElement>) {
    const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const next = options[(options.findIndex((option) => option.id === theme) + step + options.length) % options.length];
    setTheme(next.id);
    group.current?.querySelector<HTMLElement>(`[data-theme-option="${next.id}"]`)?.focus();
  }
  return <div ref={group} role="radiogroup" aria-label="Colour theme" onKeyDown={keys} className={`inline-flex items-center gap-0.5 rounded-full border bg-muted/50 p-0.5 ${className}`}>
    {options.map(({ id, label, Icon }) => {
      const selected = theme === id;
      return <button key={id} type="button" role="radio" aria-checked={selected} aria-label={label} title={label} data-theme-option={id} tabIndex={selected ? 0 : -1} onClick={() => setTheme(id)}
        className={`flex size-7 items-center justify-center rounded-full outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring/60 [@media(pointer:coarse)]:size-9 ${selected ? "bg-background text-foreground shadow-sm ring-1 ring-border" : "text-muted-foreground hover:text-foreground"}`}>
        <Icon className="size-3.5" aria-hidden="true" />
      </button>;
    })}
  </div>;
}
