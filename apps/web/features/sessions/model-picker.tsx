"use client";
import { useId, useRef, useState, useSyncExternalStore, type KeyboardEvent } from "react";
import { Check, ChevronDown, ChevronRight } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { chipClass, compactOptionClass } from "@/features/sessions/composer-styles";

import { ModelProviderIcon } from "@/features/sessions/model-provider-icon";
import { effortInfo } from "@/features/sessions/reasoning-effort";
import type { ReasoningEffort } from "@/lib/reasoning-effort-api";

export type ModelOption = { id: string; provider?: string | null };
/** How much the model thinks: the efforts it offers (none: no choice), the one in force, and the change. */
export type EffortControl = { value: ReasoningEffort | null; options: readonly ReasoningEffort[]; onChange: (effort: ReasoningEffort) => void };

const providerNames: Record<string, string> = { openai: "OpenAI", anthropic: "Anthropic", google: "Google" };
function providerName(provider: string) { return providerNames[provider.toLowerCase()] ?? provider.charAt(0).toUpperCase() + provider.slice(1); }

/** Wide enough for the effort list to sit beside the menu; on a phone it opens under the model instead. */
const WIDE = "(min-width: 640px)";
function useWide() {
  return useSyncExternalStore((notify) => {
    const query = window.matchMedia(WIDE);
    query.addEventListener("change", notify);
    return () => query.removeEventListener("change", notify);
  }, () => window.matchMedia(WIDE).matches, () => true);
}

/** The efforts as one compact radio group, with the meaning of the highlighted one underneath (instead of a hint on every row). */
function EffortList({ effort, chosen, onPick, className = "" }: { effort: EffortControl; chosen: ReasoningEffort | null; onPick: (effort: ReasoningEffort) => void; className?: string }) {
  const labelId = useId();
  const hintId = useId();
  const [pointed, setPointed] = useState<ReasoningEffort | null>(null);
  const shown = pointed ?? chosen ?? effort.options[0];
  function navigate(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    // Handled here only: the menu's own arrow keys must not also move focus back to the model list.
    event.preventDefault(); event.stopPropagation();
    const items = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('[role="radio"]'));
    const index = items.indexOf(document.activeElement as HTMLElement);
    const next = items[(index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length];
    next?.focus();
  }
  return <div className={className}>
    <p id={labelId} className="px-2.5 pb-1 pt-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">Thinking effort</p>
    <div role="radiogroup" aria-labelledby={labelId} aria-describedby={hintId} className="flex flex-col gap-px" onKeyDown={navigate} onMouseLeave={() => setPointed(null)}>
      {effort.options.map((item, index) => {
        const selected = item === chosen;
        return <button key={item} type="button" role="radio" aria-checked={selected} data-effort-radio data-effort-option={item}
          tabIndex={selected || (!chosen && index === 0) ? 0 : -1} className={compactOptionClass}
          onMouseEnter={() => setPointed(item)} onFocus={() => setPointed(item)} onBlur={() => setPointed(null)} onClick={() => onPick(item)}>
          <span className="min-w-0 flex-1 truncate font-medium">{effortInfo[item].label}</span>
          <Check className={`size-3.5 shrink-0 ${selected ? "" : "invisible"}`} aria-hidden="true" />
        </button>;
      })}
    </div>
    {/* A constant height (two lines for the meaning, one for the note): a hint of another length must never resize the list under the pointer. */}
    <div className="mt-1 border-t px-2.5 pb-1.5 pt-2 text-xs leading-4">
      <p id={hintId} className="overflow-hidden text-muted-foreground sm:h-8">{effortInfo[shown].hint}</p>
      <p className="text-muted-foreground/70">Applies to your next message.</p>
    </div>
  </div>;
}

/**
 * Model menu. Models are managed by the platform, so this lists whatever the workspace configuration offers and marks the active
 * one. The effort the model thinks with (the person's own, kept on the server) belongs to the model: hovering or opening the model
 * row shows the efforts beside the menu, so the button itself stays just the model name.
 */
export function ModelPicker({ models, value, loading, disabled, side = "top", effort }: { models: ModelOption[]; value: string | null; loading: boolean; disabled: boolean; side?: "top" | "bottom"; effort?: EffortControl }) {
  const [open, setOpen] = useState(false);
  const [effortOpen, setEffortOpen] = useState(false);
  const fromKeyboard = useRef(false);
  const wide = useWide();
  const chosen = effort?.value && effort.options.includes(effort.value) ? effort.value : null;
  const choices = effort && effort.options.length ? effort : null;
  const list = useRef<HTMLDivElement>(null);
  function navigate(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const items = Array.from(list.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? []);
    const index = items.indexOf(document.activeElement as HTMLElement);
    const next = items[Math.min(items.length - 1, Math.max(0, index + (event.key === "ArrowDown" ? 1 : -1)))];
    if (next) { event.preventDefault(); next.focus(); }
  }
  function close() { setOpen(false); setEffortOpen(false); }
  function pick(next: ReasoningEffort) {
    close();
    if (next !== chosen) choices?.onChange(next);
  }
  const label = value ?? (loading ? "Model" : "No model");
  const summary = chosen ? `${label}, ${effortInfo[chosen].label} effort` : label;
  return <Popover open={open} onOpenChange={(next) => { setOpen(next); if (!next) setEffortOpen(false); }}>
    <PopoverTrigger disabled={disabled || loading} aria-label={`Model: ${summary}`} title={`Model: ${summary}`} className={`${chipClass} justify-center max-sm:w-11 max-sm:px-0 sm:max-w-52`}>
      <ModelProviderIcon provider={models.find((model) => model.id === value)?.provider} />
      <span className="hidden min-w-0 truncate sm:inline">{label}</span>
      <ChevronDown className="hidden size-3 opacity-60 sm:block" aria-hidden="true" />
    </PopoverTrigger>
    <PopoverContent side={side} align="end" className="w-[min(15rem,calc(100vw-1.5rem))] p-1" initialFocus={(type) => type === "touch" ? true : list.current?.querySelector<HTMLElement>('[aria-selected="true"]') ?? true}>
      <div ref={list} role="listbox" aria-label="Model" className="flex flex-col gap-px" onKeyDown={navigate}>
        {models.length ? models.map((model) => {
          const selected = model.id === value;
          const withEffort = selected && !!choices;
          const body = <>
            <ModelProviderIcon provider={model.provider} className="size-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 flex-1 truncate font-medium">{model.id}{model.provider ? <span className="sr-only">, {providerName(model.provider)}</span> : null}</span>
            {withEffort && chosen ? <span className="flex shrink-0 items-center gap-0.5 text-xs text-muted-foreground">{effortInfo[chosen].label}<ChevronRight className={`size-3.5 transition-transform motion-reduce:transition-none ${!wide && effortOpen ? "rotate-90" : ""}`} aria-hidden="true" /></span>
              : <Check className={`size-3.5 shrink-0 ${selected ? "" : "invisible"}`} aria-hidden="true" />}
          </>;
          const title = model.provider ? providerName(model.provider) : undefined;
          // The active model carries the effort: beside the menu on a wide screen (on hover, click or →), under the row on a phone.
          if (withEffort && wide) return <Popover key={model.id} open={effortOpen} onOpenChange={(next) => { setEffortOpen(next); if (!next) fromKeyboard.current = false; }}>
            <PopoverTrigger openOnHover delay={80} closeDelay={140} role="option" aria-selected title={title} className={`${compactOptionClass} bg-muted/60 aria-expanded:bg-muted`}
              onKeyDown={(event) => { if (event.key === "ArrowRight") { event.preventDefault(); fromKeyboard.current = true; setEffortOpen(true); } }}>{body}</PopoverTrigger>
            <PopoverContent side="left" align={side === "top" ? "end" : "start"} sideOffset={6} className="w-56 p-1"
              initialFocus={(type) => type === "keyboard" || fromKeyboard.current ? document.querySelector<HTMLElement>("[data-effort-radio][aria-checked='true']") ?? true : false}>
              <div onKeyDown={(event) => { if (event.key === "ArrowLeft") { event.preventDefault(); event.stopPropagation(); setEffortOpen(false); } }}>
                <EffortList effort={choices} chosen={chosen} onPick={pick} />
              </div>
            </PopoverContent>
          </Popover>;
          if (withEffort) return <div key={model.id}>
            <button type="button" role="option" aria-selected title={title} className={`${compactOptionClass} bg-muted/60`} onClick={() => setEffortOpen((current) => !current)}>{body}</button>
            {effortOpen ? <EffortList effort={choices} chosen={chosen} onPick={pick} className="ml-3 border-l pl-1" /> : null}
          </div>;
          return <button key={model.id} type="button" role="option" aria-selected={selected} title={title} className={compactOptionClass} onClick={close}>{body}</button>;
        }) : <p className="px-2.5 py-2 text-[13px] leading-5 text-muted-foreground">No model is available right now. Please try again shortly.</p>}
      </div>
    </PopoverContent>
  </Popover>;
}
