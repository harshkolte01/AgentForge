"use client";
import { useEffect, useRef, useState } from "react";
import { ArrowUp, Check, ChevronDown, ChevronRight, MoreHorizontal, PanelLeftClose, Paperclip, RotateCcw, Search, ShieldQuestion, SquarePen } from "lucide-react";
import { BrandMark } from "@/components/brand-mark";
import { Button } from "@/components/ui/button";

const conversations = ["Fix date-parsing tests", "Compare vector databases", "Build a CSV converter", "Understand authentication"];
const activity = ["Searched for date parsing", "Read utils/dates.py", "Ran tests · 1 failed, 41 passed"];
const edit = 'utils/dates.py\n\n- return datetime.strptime(value, "%d/%m/%Y")\n+ return datetime.fromisoformat(value)';

/** Local illustration of the real conversation UI. Demo decisions never call workspace APIs. */
export function HeroDemo() {
  const [outcome, setOutcome] = useState<"approved" | "declined" | null>(null);
  const thread = useRef<HTMLDivElement>(null);
  const replayButton = useRef<HTMLButtonElement>(null);
  const approveButton = useRef<HTMLButtonElement>(null);
  const interacted = useRef(false);

  useEffect(() => {
    if (!interacted.current) return;
    const node = thread.current;
    if (outcome) replayButton.current?.focus({ preventScroll: true });
    else approveButton.current?.focus({ preventScroll: true });
    node?.scrollTo({ top: outcome ? node.scrollHeight : 0, behavior: "auto" });
  }, [outcome]);

  function decide(value: "approved" | "declined" | null) {
    interacted.current = true;
    setOutcome(value);
  }

  return <figure aria-label="An interactive preview of a conversation with AgentForge" className="lp-window mx-auto overflow-hidden rounded-2xl text-left">
    <div className="flex">
      <aside className="hidden w-56 shrink-0 flex-col bg-sidebar p-3 md:flex" aria-hidden="true">
        <div className="mb-5 flex min-h-9 items-center justify-between px-1"><span className="flex items-center gap-2 text-[13px] font-semibold"><BrandMark className="size-6" />AgentForge</span><PanelLeftClose className="size-3.5 text-muted-foreground" /></div>
        <div className="mb-3 flex min-h-9 items-center gap-2 px-2 text-[13px]"><SquarePen className="size-4" />New conversation</div>
        <div className="mb-3 flex min-h-9 items-center gap-2 rounded-lg bg-background/50 px-2 text-xs text-muted-foreground"><Search className="size-3.5" />Search conversations</div>
        <ul className="space-y-0.5">{conversations.map((title, index) => <li key={title} className={`flex min-h-10 items-center rounded-lg px-2 text-[13px] ${index === 0 ? "bg-sidebar-accent text-foreground" : "text-muted-foreground"}`}>{title}</li>)}</ul>
        <div className="mt-auto flex items-center gap-2 px-2 pt-8 text-[13px]"><span className="flex size-7 items-center justify-center rounded-full bg-muted text-xs">Y</span>You<MoreHorizontal className="ml-auto size-4 text-muted-foreground" /></div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col bg-background">
        <div className="flex h-10 shrink-0 items-center justify-between px-5 sm:px-8">
          <span className="text-xs text-muted-foreground md:invisible">AgentForge</span>
          <span className="text-[11px] text-muted-foreground">Interactive demo</span>
        </div>
        <div ref={thread} tabIndex={0} role="region" aria-label="Sample conversation" className="max-h-[36rem] overflow-y-auto overscroll-contain sm:h-[27rem]">
          <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-5 pb-6 pt-4 sm:px-8">
            <div className="flex justify-end"><p className="max-w-[85%] whitespace-pre-wrap break-words rounded-3xl bg-muted px-4 py-2.5 text-[15px] leading-7">Tests fail after the date-parsing change. Find the cause and fix it.</p></div>
            {outcome ? <details className="group">
              <summary className="flex min-h-11 cursor-pointer list-none items-center gap-1 rounded-lg text-[13px] text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden"><ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden="true" />Worked · {outcome === "approved" ? "5" : "3"} steps</summary>
              <ul className="mt-2 space-y-2 text-sm text-muted-foreground">{[...activity, ...(outcome === "approved" ? ["Updated date parsing", "Ran tests · 42 passed"] : [])].map(label => <li key={label} className="flex items-center gap-2.5"><Check className="size-3.5 shrink-0" aria-hidden="true" />{label}</li>)}</ul>
            </details> : <ul aria-label="Sample activity" className="space-y-2 text-sm leading-6 text-muted-foreground">{activity.map(label => <li key={label} className="flex items-center gap-2.5"><Check className="size-3.5 shrink-0" aria-hidden="true" />{label}</li>)}</ul>}
            {!outcome ? <div role="group" aria-label="Edit this file?" className="flex min-w-0 flex-col gap-2.5 rounded-2xl border bg-card p-3.5">
              <p className="flex items-center gap-2 text-[13px] font-medium leading-5"><ShieldQuestion className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />Edit this file?</p>
              <pre className="min-w-0 whitespace-pre-wrap break-words rounded-lg bg-muted px-3 py-2 font-mono text-[12.5px] leading-5">{edit}</pre>
              <div className="flex items-center justify-end gap-2">
                <Button type="button" variant="ghost" size="sm" className="min-h-10 px-3" onClick={() => decide("declined")}>Decline</Button>
                <Button ref={approveButton} type="button" size="sm" className="min-h-10 px-3" onClick={() => decide("approved")}>Approve</Button>
              </div>
            </div> : <div className="space-y-4 text-[15px] leading-7">
              <p role="status">{outcome === "approved" ? "Fixed the date format mismatch and reran the tests. All 42 tests pass in this example." : "No changes made. The parser expects day-first dates, but the input uses ISO format. We can try a different approach."}</p>
              <Button ref={replayButton} type="button" variant="ghost" size="sm" className="min-h-11 text-muted-foreground" onClick={() => decide(null)}><RotateCcw />Replay</Button>
            </div>}
          </div>
        </div>
        <div className="mx-auto w-full max-w-3xl px-4 pb-5 pt-3 sm:px-8" aria-hidden="true">
          <div className="rounded-3xl border bg-card">
            <p className="min-h-[3.75rem] px-5 pb-1 pt-4 text-[15px] leading-6 text-muted-foreground">Ask anything, or describe what you want to build…</p>
            <div className="flex items-center justify-between gap-2 px-3 pb-3 pt-1 text-xs text-muted-foreground">
              <Paperclip className="size-4" />
              <div className="flex items-center gap-3"><ShieldQuestion className="size-4" /><span className="flex items-center gap-1">Model<ChevronDown className="size-3" /></span><span className="flex size-8 items-center justify-center rounded-full bg-primary text-primary-foreground"><ArrowUp className="size-4" /></span></div>
            </div>
          </div>
          <p className="mt-2 text-center text-[11px] leading-5 text-muted-foreground">Agents can make mistakes. Review important results.</p>
        </div>
      </div>
    </div>
  </figure>;
}
