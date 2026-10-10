"use client";
import { useId, useRef, useState, type KeyboardEvent } from "react";
import { Check, ChevronDown, Cpu } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { chipClass, optionClass } from "@/features/sessions/composer-styles";

export type ModelOption = { id: string; provider?: string | null };

const providerNames: Record<string, string> = { openai: "OpenAI", anthropic: "Anthropic", google: "Google" };
function providerName(provider: string) { return providerNames[provider.toLowerCase()] ?? provider.charAt(0).toUpperCase() + provider.slice(1); }

/**
 * Model menu. Models are managed by the platform, so this lists whatever the
 * workspace configuration offers and marks the active one. It stays a menu (not
 * a settings form) so it is ready for more than one model.
 */
export function ModelPicker({ models, value, loading, disabled, side = "top" }: { models: ModelOption[]; value: string | null; loading: boolean; disabled: boolean; side?: "top" | "bottom" }) {
  const [open, setOpen] = useState(false);
  const labelId = useId();
  const list = useRef<HTMLDivElement>(null);
  function navigate(event: KeyboardEvent<HTMLElement>) {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const items = Array.from(list.current?.querySelectorAll<HTMLElement>('[role="option"]') ?? []);
    const index = items.indexOf(document.activeElement as HTMLElement);
    const next = items[Math.min(items.length - 1, Math.max(0, index + (event.key === "ArrowDown" ? 1 : -1)))];
    if (next) { event.preventDefault(); next.focus(); }
  }
  const label = value ?? (loading ? "Model" : "No model");
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger disabled={disabled || loading} aria-label={`Model: ${label}`} title={`Model: ${label}`} className={`${chipClass} max-w-40 justify-center max-sm:w-10 max-sm:px-0 sm:max-w-56`}>
      <Cpu className="size-4" aria-hidden="true" />
      <span className="hidden min-w-0 truncate sm:inline">{label}</span>
      <ChevronDown className="hidden size-3 opacity-60 sm:block" aria-hidden="true" />
    </PopoverTrigger>
    <PopoverContent side={side} align="end" className="w-[min(17rem,calc(100vw-1.5rem))]" initialFocus={(type) => type === "touch" ? true : list.current?.querySelector<HTMLElement>('[aria-selected="true"]') ?? true}>
      <div className="p-1.5" onKeyDown={navigate}>
        <p className="px-2.5 pb-1 pt-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground" id={labelId}>Model</p>
        <div ref={list} role="listbox" aria-labelledby={labelId} className="flex flex-col gap-px">
          {models.length ? models.map((model) => {
            const selected = model.id === value;
            return <button key={model.id} type="button" role="option" aria-selected={selected} className={optionClass} onClick={() => setOpen(false)}>
              <Cpu className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
              <span className="min-w-0 flex-1"><span className="block truncate text-[13px] font-medium leading-5">{model.id}</span>{model.provider ? <span className="block truncate text-xs leading-4 text-muted-foreground">{providerName(model.provider)}</span> : null}</span>
              <Check className={`size-4 shrink-0 ${selected ? "" : "invisible"}`} aria-hidden="true" />
            </button>;
          }) : <p className="px-2.5 py-2 text-[13px] leading-5 text-muted-foreground">No model is available right now. Please try again shortly.</p>}
        </div>
      </div>
    </PopoverContent>
  </Popover>;
}
