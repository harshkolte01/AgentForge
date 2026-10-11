"use client";
import { useEffect, useId, useState, useSyncExternalStore } from "react";
import { Check, ChevronDown, ShieldCheck, Zap } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import type { ApprovalMode } from "@/lib/activity/approvals";
import { createApprovalModeStore, type ApprovalModeState } from "@/lib/approval-mode-store";
import { fetchApprovalMode, saveApprovalMode } from "@/lib/approvals-api";
import { chipClass, optionClass } from "@/features/sessions/composer-styles";

export const approvalModes: { id: ApprovalMode; label: string; hint: string; Icon: typeof ShieldCheck }[] = [
  { id: "ask", label: "Ask me", hint: "Asks before it runs a command, changes a file or opens a pull request.", Icon: ShieldCheck },
  { id: "auto", label: "Approve for me", hint: "Does everything without asking.", Icon: Zap },
];

/** One copy for the page: every composer and Settings share it, so switching conversations never reads it again. */
const approvalModeStore = createApprovalModeStore({ fetch: () => fetchApprovalMode(), save: saveApprovalMode });
const idle: ApprovalModeState = { mode: null, status: "idle" };
if (typeof window !== "undefined") window.addEventListener("agentforge-session-expired", () => approvalModeStore.reset());

/** The approval mode, read on first use. `choose` shows the new mode at once and rejects if it could not be saved. */
export function useApprovalMode() {
  const state = useSyncExternalStore(approvalModeStore.subscribe, approvalModeStore.getSnapshot, () => idle);
  useEffect(() => { approvalModeStore.load(); }, []);
  return { ...state, choose: approvalModeStore.choose, retry: approvalModeStore.load };
}

/**
 * One choice for every conversation: whether the agent stops to ask before anything that changes something. Reading and
 * searching never ask. The choice is kept on the server, so it follows the person to every conversation and device.
 */
export function ApprovalModePicker({ side = "top", compact = false }: { side?: "top" | "bottom"; compact?: boolean }) {
  const descriptionId = useId();
  const { mode, status, choose, retry } = useApprovalMode();
  const [open, setOpen] = useState(false);
  function pick(next: ApprovalMode) {
    setOpen(false);
    if (next !== mode) void choose(next).catch(() => { /* The previous mode is shown again. */ });
  }
  const current = approvalModes.find((item) => item.id === (mode ?? "ask")) ?? approvalModes[0];
  // Opening the menu after a failed read tries the read again; the menu still works with the default meanwhile.
  return <Popover open={open} onOpenChange={(next) => { setOpen(next); if (next && status === "error") retry(); }}>
    <PopoverTrigger disabled={status === "idle" || (status === "loading" && mode === null)} aria-label={`Approvals: ${current.label}`} title={`Approvals: ${current.label}`} className={`${chipClass} justify-center ${compact ? "max-sm:w-11 max-sm:px-0" : ""}`}>
      <current.Icon className="size-4" aria-hidden="true" />
      {/* Sized to the longest label so the row never shifts when the mode loads or changes. */}
      <span className={`grid whitespace-nowrap ${compact ? "hidden sm:grid" : ""}`}>{approvalModes.map((item) => <span key={item.id} aria-hidden={item.id !== current.id || undefined} className={`col-start-1 row-start-1 ${item.id === current.id ? "" : "invisible"}`}>{item.label}</span>)}</span>
      <ChevronDown className={`size-3 opacity-60 ${compact ? "hidden sm:block" : ""}`} aria-hidden="true" />
    </PopoverTrigger>
    <PopoverContent side={side} align="end" className="w-[min(13rem,calc(100vw-1.5rem))] rounded-xl shadow-sm" initialFocus={(type) => type === "touch" ? true : document.querySelector<HTMLElement>("[data-approval-option][aria-checked='true']") ?? true}>
      <div className="p-1" role="radiogroup" aria-label="When the agent may act">
        {approvalModes.map(({ id, label, hint }) => <button key={id} type="button" role="radio" aria-checked={id === current.id} data-approval-option aria-describedby={`${descriptionId}-${id}`} title={hint} className={optionClass} onClick={() => pick(id)}>
          <span className="min-w-0 flex-1">{label}</span>
          <span id={`${descriptionId}-${id}`} className="sr-only">{hint}</span>
          <Check className={`size-3.5 shrink-0 ${id === current.id ? "" : "invisible"}`} aria-hidden="true" />
        </button>)}
      </div>
    </PopoverContent>
  </Popover>;
}
