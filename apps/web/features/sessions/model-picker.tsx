"use client";
import { useId, useRef, useState, type KeyboardEvent } from "react";
import { Check, ChevronDown } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { chipClass, optionClass } from "@/features/sessions/composer-styles";

import { ModelProviderIcon } from "@/features/sessions/model-provider-icon";
import { effortInfo } from "@/features/sessions/reasoning-effort";
import type { ReasoningEffort } from "@/lib/reasoning-effort-api";

export type ModelOption = { id: string; provider?: string | null };
/** How much the model thinks: the efforts it offers (none: no section), the one in force, and the change. */
export type EffortControl = { value: ReasoningEffort | null; options: readonly ReasoningEffort[]; onChange: (effort: ReasoningEffort) => void };

const providerNames: Record<string, string> = { openai: "OpenAI", anthropic: "Anthropic", google: "Google" };
function providerName(provider: string) { return providerNames[provider.toLowerCase()] ?? provider.charAt(0).toUpperCase() + provider.slice(1); }

/**
 * Model menu. Models are managed by the platform, so this lists whatever the
 * workspace configuration offers and marks the active one. It stays a menu (not
 * a settings form) so it is ready for more than one model. Under the models, the effort the
 * model thinks with (when it offers a choice): the person's own, kept on the server.
 */
export function ModelPicker({ models, value, loading, disabled, side = "top", effort }: { models: ModelOption[]; value: string | null; loading: boolean; disabled: boolean; side?: "top" | "bottom"; effort?: EffortControl }) {
  const [open, setOpen] = useState(false);
  const labelId = useId();
  const effortLabelId = useId();
  const hintId = useId();
  const chosen = effort?.value && effort.options.includes(effort.value) ? effort.value : null;
  const list = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  function navigate(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const items = Array.from(menu.current?.querySelectorAll<HTMLElement>('[role="option"], [role="radio"]') ?? []);
    const index = items.indexOf(document.activeElement as HTMLElement);
    const next = items[Math.min(items.length - 1, Math.max(0, index + (event.key === "ArrowDown" ? 1 : -1)))];
    if (next) { event.preventDefault(); next.focus(); }
  }
  const label = value ?? (loading ? "Model" : "No model");
  const summary = chosen ? `${label}, ${effortInfo[chosen].label} effort` : label;
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger disabled={disabled || loading} aria-label={`Model: ${summary}`} title={`Model: ${summary}`} className={`${chipClass} max-w-40 justify-center max-sm:w-11 max-sm:px-0 sm:max-w-56`}>
      <ModelProviderIcon provider={models.find((model) => model.id === value)?.provider} />
      <span className="hidden min-w-0 truncate sm:inline">{label}</span>
      {/* Sized to the longest effort name, so the row never shifts when the effort changes. */}
      {chosen && effort ? <span className="hidden shrink-0 grid-cols-1 whitespace-nowrap text-muted-foreground/80 sm:grid" data-effort-label>{effort.options.map((item) => <span key={item} aria-hidden={item !== chosen || undefined} className={`col-start-1 row-start-1 ${item === chosen ? "" : "invisible"}`}>{effortInfo[item].label}</span>)}</span> : null}
      <ChevronDown className="hidden size-3 opacity-60 sm:block" aria-hidden="true" />
    </PopoverTrigger>
    <PopoverContent side={side} align="end" className="w-[min(19rem,calc(100vw-1.5rem))]" initialFocus={(type) => type === "touch" ? true : list.current?.querySelector<HTMLElement>('[aria-selected="true"]') ?? true}>
      <div ref={menu} className="p-1.5" onKeyDown={navigate}>
        <p className="px-2.5 pb-1 pt-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground" id={labelId}>Model</p>
        <div ref={list} role="listbox" aria-labelledby={labelId} className="flex flex-col gap-px">
          {models.length ? models.map((model) => {
            const selected = model.id === value;
            return <button key={model.id} type="button" role="option" aria-selected={selected} className={optionClass} onClick={() => setOpen(false)}>
              <ModelProviderIcon provider={model.provider} className="size-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1"><span className="block truncate text-[13px] font-medium leading-5">{model.id}</span>{model.provider ? <span className="block truncate text-xs leading-4 text-muted-foreground">{providerName(model.provider)}</span> : null}</span>
              <Check className={`size-4 shrink-0 ${selected ? "" : "invisible"}`} aria-hidden="true" />
            </button>;
          }) : <p className="px-2.5 py-2 text-[13px] leading-5 text-muted-foreground">No model is available right now. Please try again shortly.</p>}
        </div>
        {effort && effort.options.length ? <div className="mt-1.5 border-t pt-1.5">
          <p className="px-2.5 pb-1 pt-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground" id={effortLabelId}>Effort</p>
          <div role="radiogroup" aria-labelledby={effortLabelId} aria-describedby={hintId} className="flex flex-col gap-px">
            {effort.options.map((item, index) => {
              const selected = item === chosen;
              return <button key={item} type="button" role="radio" aria-checked={selected} tabIndex={selected || (!chosen && index === 0) ? 0 : -1} data-effort-option={item} title={effortInfo[item].hint} className={optionClass}
                onClick={() => { setOpen(false); if (!selected) effort.onChange(item); }}>
                <span className="min-w-0 flex-1"><span className="block truncate text-[13px] font-medium leading-5">{effortInfo[item].label}</span><span className="block truncate text-xs leading-4 text-muted-foreground">{effortInfo[item].hint}</span></span>
                <Check className={`size-4 shrink-0 ${selected ? "" : "invisible"}`} aria-hidden="true" />
              </button>;
            })}
          </div>
          <p id={hintId} className="px-2.5 pb-1.5 pt-1.5 text-xs leading-4 text-muted-foreground">Applies from your next message.</p>
        </div> : null}
      </div>
    </PopoverContent>
  </Popover>;
}
