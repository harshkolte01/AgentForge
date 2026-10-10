"use client";
import { useState, type RefObject } from "react";
import { Info } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { chipClass } from "@/features/sessions/composer-styles";
import { useContextStatus } from "@/features/sessions/use-context-status";
import { coverageText, formatTokens, levelText, projectContext, type ContextLevel, type ContextProjection, type ContextStatus } from "@/lib/context";

const RADIUS = 7.5;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
const tone: Record<ContextLevel, { stroke: string; fill: string; text: string }> = {
  ok: { stroke: "stroke-foreground/75", fill: "bg-foreground/75", text: "text-muted-foreground" },
  notice: { stroke: "stroke-amber-500", fill: "bg-amber-500", text: "text-amber-600 dark:text-amber-400" },
  high: { stroke: "stroke-destructive", fill: "bg-destructive", text: "text-destructive" },
  unknown: { stroke: "stroke-muted-foreground/60", fill: "bg-muted-foreground/60", text: "text-muted-foreground" },
};

/** A gauge ring: an empty track until something is used, then an arc that grows with the share of the window. */
function Ring({ percent, level, unavailable }: { percent: number; level: ContextLevel; unavailable: boolean }) {
  const shown = unavailable || percent <= 0 ? 0 : Math.max(percent, 4);
  return <svg viewBox="0 0 20 20" className="size-[18px] -rotate-90" aria-hidden="true">
    <circle cx="10" cy="10" r={RADIUS} fill="none" strokeWidth="2.5" className={`stroke-muted-foreground/30 ${unavailable ? "[stroke-dasharray:2_3]" : ""}`} />
    {shown ? <circle cx="10" cy="10" r={RADIUS} fill="none" strokeWidth="2.5" strokeLinecap="round" strokeDasharray={CIRCUMFERENCE} strokeDashoffset={CIRCUMFERENCE * (1 - shown / 100)} className={`${tone[level].stroke} transition-[stroke-dashoffset] duration-500 ease-out motion-reduce:transition-none`} /> : null}
  </svg>;
}

type Segment = { tokens: number; className: string };

/** The bar splits into what is in the window: the run's measured peak, or instructions, history and the draft. */
function segmentsFor(status: ContextStatus, projected: ContextProjection, draftTokens: number): Segment[] {
  const head: Segment[] = status.used_basis === "last_run_peak"
    ? [{ tokens: status.used_tokens, className: tone[projected.level].fill }]
    : [{ tokens: status.baseline_tokens, className: "bg-foreground/75" }, { tokens: status.history_tokens, className: "bg-foreground/45" }];
  return [...head, { tokens: draftTokens, className: "bg-foreground/25" }].filter((segment) => segment.tokens > 0);
}

function Legend({ color, label, value, title }: { color: string; label: string; value: string; title?: string }) {
  return <li className="flex items-center gap-2" title={title}>
    <span className={`size-2 shrink-0 rounded-full ${color}`} aria-hidden="true" />
    <span className="min-w-0 flex-1 truncate text-muted-foreground">{label}</span>
    <span className="shrink-0 font-medium tabular-nums">{value}</span>
  </li>;
}

function Details({ status, draftTokens, onNewConversation, close }: { status: ContextStatus; draftTokens: number; onNewConversation?: () => void; close: () => void }) {
  const projected = projectContext(status, draftTokens);
  const windowTokens = status.window_tokens;
  const last = status.last_run;
  const peakBasis = status.used_basis === "last_run_peak";
  const percentText = projected.percent > 0 && projected.percent < 10 ? projected.percent.toFixed(1) : String(Math.round(projected.percent));
  const segments = windowTokens ? segmentsFor(status, projected, draftTokens) : [];
  return <div className="p-3.5 text-xs leading-5">
    <div className="flex items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-[13px] font-semibold leading-5">Context window</h2>
        <p className="truncate text-muted-foreground">{status.model ?? "No model"}{windowTokens ? ` · ${formatTokens(windowTokens)} tokens` : ""}</p>
      </div>
      {windowTokens ? <p className={`shrink-0 text-lg font-semibold leading-6 tabular-nums ${projected.empty ? "text-muted-foreground" : ""}`}>{percentText}%</p> : null}
    </div>
    {windowTokens ? <>
      <div role="meter" aria-label="Context window used" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(projected.percent)} aria-valuetext={`${percentText}% of ${windowTokens} tokens`} className="mt-2.5 flex h-1.5 gap-px overflow-hidden rounded-full bg-muted">
        {segments.map((segment, index) => <div key={index} className={`h-full first:rounded-l-full last:rounded-r-full ${segment.className} transition-[width] duration-500 ease-out motion-reduce:transition-none`} style={{ width: `${Math.max(Math.min(100, segment.tokens / windowTokens * 100), 1)}%` }} />)}
      </div>
      <p className="mt-1.5 flex items-baseline justify-between gap-3">
        <span className="tabular-nums text-muted-foreground">{projected.empty ? "Nothing sent yet" : `${peakBasis && !draftTokens ? "" : "≈ "}${formatTokens(projected.used)} of ${formatTokens(windowTokens)}`}</span>
        <span className={`font-medium ${tone[projected.level].text}`}>{projected.empty ? "Empty" : levelText[projected.level]}</span>
      </p>
    </> : <p className="mt-2.5 text-muted-foreground">This model&apos;s context window size isn&apos;t configured, so usage can&apos;t be shown.</p>}
    {projected.empty ? <p className="mt-3 border-t border-border/60 pt-3 text-muted-foreground">Each message starts with about {formatTokens(status.baseline_tokens)} tokens of instructions and tools. Nothing counts until you send one.</p> : <>
      <ul className="mt-3 space-y-1.5 border-t border-border/60 pt-3">
        <Legend color="bg-foreground/75" label="Instructions and tools" value={`≈ ${formatTokens(status.baseline_tokens)}`} title="Sent with every message" />
        <Legend color="bg-foreground/45" label="Conversation history" value={status.history_tokens ? `≈ ${formatTokens(status.history_tokens)}` : "None"} title={`Up to ≈ ${formatTokens(status.history_budget_tokens)} of history is carried into each message`} />
        {draftTokens > 0 ? <Legend color="bg-foreground/25" label="Your draft" value={`≈ ${formatTokens(draftTokens)}`} /> : null}
        {last ? <Legend color={tone[projected.level].fill} label="Latest run, peak" value={formatTokens(last.peak_input_tokens)} title={`Measured by the model provider over ${last.model_calls} model call${last.model_calls === 1 ? "" : "s"}`} /> : null}
      </ul>
      <p className="mt-3 text-muted-foreground">{coverageText(status)}{status.messages_condensed || status.messages_omitted ? " Older messages are shortened to keep the thread." : ""}{last?.compacted_results ? " Old tool results were condensed in the latest run." : ""}</p>
    </>}
    {projected.level === "high" ? <div className="mt-3 rounded-lg bg-destructive/10 p-2.5"><p className="font-medium text-destructive">Close to the model&apos;s limit. A new conversation will work best.</p>{onNewConversation ? <button type="button" onClick={() => { close(); onNewConversation(); }} className="mt-2 inline-flex min-h-11 w-full items-center justify-center rounded-md bg-primary px-3 text-[13px] font-medium text-primary-foreground outline-none transition-colors hover:bg-primary/85 focus-visible:ring-2 focus-visible:ring-ring/60 sm:min-h-8">Start a new conversation</button> : null}</div> : null}
    {projected.empty ? null : <p className="mt-3 text-[11px] leading-4 text-muted-foreground/80">≈ estimated; peaks are measured by the provider.</p>}
  </div>;
}

/**
 * Context window indicator for the composer: a gauge ring that opens a compact details card.
 *
 * The card anchors to the whole composer (`anchorRef`), not the small ring, so it opens clear above
 * it instead of covering what is being typed. It is informational only and never blocks sending.
 */
export function ContextStatusButton({ sessionId, revision, live, draftTokens, anchorRef, side = "top", onNewConversation }: { sessionId: string | null; revision: string; live: boolean; draftTokens: number; anchorRef?: RefObject<Element | null>; side?: "top" | "bottom"; onNewConversation?: () => void }) {
  const [open, setOpen] = useState(false);
  const { status, failed, refresh } = useContextStatus(sessionId, revision, live);
  const projected = status ? projectContext(status, draftTokens) : null;
  const level = projected?.level ?? "unknown";
  const label = !status ? (failed ? "Context window: unavailable" : "Context window: loading")
    : !status.window_tokens ? "Context window: size not configured"
    : projected?.empty ? "Context window: empty" : `Context window: ${projected?.percent}% used, ${levelText[level].toLowerCase()}`;
  const flagged = !!status && !projected?.empty && (level === "notice" || level === "high");
  return <Popover open={open} onOpenChange={(next) => { setOpen(next); if (next) refresh(); }}>
    <PopoverTrigger aria-label={label} title={label} className={`${chipClass} gap-1.5 max-sm:w-10 max-sm:justify-center max-sm:px-0 ${flagged ? tone[level].text : ""}`}>
      <Ring percent={projected?.percent ?? 0} level={level} unavailable={!status || level === "unknown"} />
      {flagged && projected ? <span className="hidden text-xs font-medium tabular-nums sm:inline">{Math.round(projected.percent)}%</span> : null}
    </PopoverTrigger>
    <PopoverContent side={side} align="end" sideOffset={10} anchor={anchorRef} className="w-[min(18.5rem,calc(100vw-1.5rem))]">
      {status ? <Details status={status} draftTokens={draftTokens} onNewConversation={onNewConversation} close={() => setOpen(false)} />
        : <div className="flex items-start gap-2.5 p-3.5 text-xs leading-5 text-muted-foreground"><Info className="mt-0.5 size-4 shrink-0" aria-hidden="true" /><div><p>{failed ? "Context details are unavailable right now." : "Loading context details…"}</p>{failed ? <button type="button" onClick={refresh} className="mt-1 rounded text-foreground underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring/60">Try again</button> : null}</div></div>}
    </PopoverContent>
    {level === "high" && flagged ? <span className="sr-only" role="status">Context window is nearly full.</span> : null}
  </Popover>;
}
