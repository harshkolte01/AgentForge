"use client";
import { useEffect, useId, useState } from "react";
import { Check, ChevronDown, ShieldCheck, Zap } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { ApprovalMode } from "@/lib/activity/approvals";
import { fetchApprovalMode, saveApprovalMode } from "@/lib/approvals-api";
import { chipClass, optionClass } from "@/features/sessions/composer-styles";

const modes: { id: ApprovalMode; label: string; hint: string; Icon: typeof ShieldCheck }[] = [
  { id: "ask", label: "Ask me", hint: "Asks before it runs a command, changes a file or opens a pull request.", Icon: ShieldCheck },
  { id: "auto", label: "Approve for me", hint: "Does everything without asking.", Icon: Zap },
];

/**
 * One choice for every conversation: whether the agent stops to ask before anything that changes something. Reading and
 * searching never ask. The choice is kept on the server, so it follows the person to every conversation and device.
 */
export function ApprovalModePicker({ side = "top", compact = false }: { side?: "top" | "bottom"; compact?: boolean }) {
  const descriptionId = useId();
  const [mode, setMode] = useState<ApprovalMode | null>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    fetchApprovalMode({ signal: controller.signal }).then((value) => setMode(value.mode), () => { /* The server's default (ask) still applies. */ });
    return () => controller.abort();
  }, []);
  function choose(next: ApprovalMode) {
    setOpen(false);
    if (next === mode) return;
    const previous = mode;
    setMode(next);
    saveApprovalMode(next).then((saved) => setMode(saved.mode), () => setMode(previous));
  }
  const current = modes.find((item) => item.id === (mode ?? "ask")) ?? modes[0];
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger disabled={mode === null} aria-label={`Approvals: ${current.label}`} title={`Approvals: ${current.label}`} className={`${chipClass} justify-center ${compact ? "max-sm:w-11 max-sm:px-0" : ""}`}>
      <current.Icon className="size-4" aria-hidden="true" />
      <span className={`whitespace-nowrap ${compact ? "hidden sm:inline" : ""}`}>{current.label}</span>
      <ChevronDown className={`size-3 opacity-60 ${compact ? "hidden sm:block" : ""}`} aria-hidden="true" />
    </PopoverTrigger>
    <PopoverContent side={side} align="end" className="w-[min(13rem,calc(100vw-1.5rem))] rounded-xl shadow-sm" initialFocus={(type) => type === "touch" ? true : document.querySelector<HTMLElement>("[data-approval-option][aria-checked='true']") ?? true}>
      <div className="p-1" role="radiogroup" aria-label="When the agent may act">
        {modes.map(({ id, label, hint }) => <button key={id} type="button" role="radio" aria-checked={id === current.id} data-approval-option aria-describedby={`${descriptionId}-${id}`} title={hint} className={optionClass} onClick={() => choose(id)}>
          <span className="min-w-0 flex-1">{label}</span>
          <span id={`${descriptionId}-${id}`} className="sr-only">{hint}</span>
          <Check className={`size-3.5 shrink-0 ${id === current.id ? "" : "invisible"}`} aria-hidden="true" />
        </button>)}
      </div>
    </PopoverContent>
  </Popover>;
}
