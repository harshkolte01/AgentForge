"use client";

import { useId, useState } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { useContextStatus } from "@/features/sessions/use-context-status";
import { formatTokens, projectContext, type ContextLevel, type ContextStatus } from "@/lib/context";

const RADIUS = 6;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
const tone: Record<ContextLevel, string> = {
  ok: "text-muted-foreground",
  notice: "text-amber-700 dark:text-amber-400",
  high: "text-destructive",
  unknown: "text-muted-foreground",
};

function Ring({ percent, unavailable }: { percent: number; unavailable: boolean }) {
  return <svg viewBox="0 0 16 16" className="size-4 -rotate-90" aria-hidden="true">
    <circle cx="8" cy="8" r={RADIUS} fill="none" strokeWidth="1.5" className="stroke-current opacity-20" />
    {!unavailable && percent > 0 ? <circle cx="8" cy="8" r={RADIUS} fill="none" strokeWidth="1.5" strokeLinecap="round"
      strokeDasharray={CIRCUMFERENCE} strokeDashoffset={CIRCUMFERENCE * (1 - Math.min(100, Math.max(percent, 2)) / 100)}
      className="stroke-current transition-[stroke-dashoffset] duration-300 ease-out motion-reduce:transition-none" /> : null}
    {unavailable ? <circle cx="8" cy="8" r="1" fill="currentColor" /> : null}
  </svg>;
}

function Details({ status, draftTokens, failed, refresh, onNewConversation, close }: {
  status: ContextStatus; draftTokens: number; failed: boolean; refresh: () => void;
  onNewConversation?: () => void; close: () => void;
}) {
  const projected = projectContext(status, draftTokens);
  const available = status.input_limit_tokens ?? (status.window_tokens ? status.window_tokens - (status.max_output_tokens ?? 0) : null);
  const percent = projected.percent.toFixed(1).replace(/\.0$/, "");
  const used = projected.empty ? 0 : projected.used;

  return <div className="px-3.5 py-3 text-xs leading-4">
    <div className="flex items-center justify-between gap-4">
      <h2 className="text-[13px] font-medium leading-5">Context</h2>
      {available ? <span className={`tabular-nums ${tone[projected.level]}`}>{percent}% used</span> : null}
    </div>
    {available ? <>
      <div role="meter" aria-label="Estimated next message input" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={projected.percent} aria-valuetext={`Approximately ${used} of ${available} input tokens`}
        className="my-2.5 h-1 overflow-hidden rounded-full bg-muted">
        <div className={`h-full rounded-full transition-[width] duration-200 ease-out motion-reduce:transition-none ${projected.level === "high" ? "bg-destructive" : projected.level === "notice" ? "bg-amber-600 dark:bg-amber-400" : "bg-foreground/60"}`}
          style={{ width: `${projected.percent}%` }} />
      </div>
      <p className="tabular-nums text-muted-foreground">{used ? `≈ ${formatTokens(used)}` : "0"} / {formatTokens(available)} tokens</p>
      <span className="sr-only">Estimated input for your next message. Space for a reply is reserved.</span>
    </> : <p className="mt-2 text-muted-foreground">Context limit unavailable.</p>}
    {status.uncovered_turns ? <p className="mt-2 text-muted-foreground">Some earlier details need retrieval.</p>
      : status.summary_through_ordinal ? <p className="mt-2 text-muted-foreground">Earlier decisions summarized.</p> : null}
    {projected.level === "high" ? <div className="mt-2.5 flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
      <p className="text-destructive">Shorten your message to free space.</p>
      {onNewConversation ? <button type="button" onClick={() => { close(); onNewConversation(); }}
        className="min-h-8 rounded px-1 font-medium outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/60 motion-reduce:transition-none">New conversation</button> : null}
    </div> : null}
    {failed ? <p role="status" className="mt-2 flex items-center justify-between gap-2 text-muted-foreground">Last known estimate <button type="button" onClick={refresh} className="min-h-8 rounded px-1 text-foreground underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/60">Retry</button></p> : null}
  </div>;
}

/** An informational hover/click popover; never blocks composing or sending. */
export function ContextStatusButton({ sessionId, revision, live, draftTokens, side = "top", onNewConversation }: {
  sessionId: string | null; revision: string; live: boolean; draftTokens: number;
  side?: "top" | "bottom"; onNewConversation?: () => void;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const { status, failed, refresh } = useContextStatus(sessionId, revision, live);
  const projected = status ? projectContext(status, draftTokens) : null;
  const level = projected?.level ?? "unknown";
  const label = !status ? (failed ? "Context unavailable" : "Loading context") : !status.window_tokens ? "Context limit not configured"
    : projected?.empty ? "Context: ready for your first message" : `Context: approximately ${projected?.percent}% of input capacity for your next message`;

  return <Popover open={open} onOpenChange={(next) => { setOpen(next); if (next) refresh(); }}>
    <PopoverTrigger id={id} aria-label={label} openOnHover delay={350} closeDelay={250}
      className={`inline-flex size-11 shrink-0 items-center justify-center rounded-full outline-none transition-colors duration-150 hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 aria-expanded:bg-muted aria-expanded:text-foreground motion-reduce:transition-none pointer-fine:sm:size-8 ${tone[level]}`}>
      <Ring percent={projected?.percent ?? 0} unavailable={!status || level === "unknown"} />
    </PopoverTrigger>
    <PopoverContent side={side} align="center" sideOffset={6} positionMethod="fixed"
      collisionAvoidance={{ side: "flip", align: "shift", fallbackAxisSide: "none" }}
      initialFocus={(interaction) => interaction === "keyboard"} aria-label="Context details"
      className="w-[min(14rem,calc(100vw-1.5rem))] rounded-xl border-border/60 shadow-[0_4px_24px_-6px_rgb(0_0_0/0.14)]">
      {status ? <Details status={status} draftTokens={draftTokens} failed={failed} refresh={refresh} onNewConversation={onNewConversation} close={() => setOpen(false)} />
        : <div className="px-3.5 py-3 text-xs leading-4"><h2 className="text-[13px] font-medium leading-5">Context</h2><p className="mt-2 text-muted-foreground" role="status">{failed ? "Unavailable right now." : "Loading…"}</p>
          {failed ? <button type="button" onClick={refresh} className="mt-1 min-h-8 rounded underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/60">Retry</button> : null}</div>}
    </PopoverContent>
    {level === "high" ? <span className="sr-only" role="status">Your next message is close to the input limit.</span> : null}
  </Popover>;
}
