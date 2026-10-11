"use client";
import { memo, useEffect, useId, useState } from "react";
import { Glyph, chevron, draftedCalls, toggleClass } from "@/features/sessions/row-parts";
import { SubagentPanel } from "@/features/sessions/subagent-panel";
import { fleetSummary, formatDuration, subagentNow, subagentSteps, subagentStatus } from "@/features/sessions/timeline-model";
import { useDisclosure } from "@/features/sessions/use-disclosure";
import { disclosureKey } from "@/lib/activity/disclosure";
import type { CallEntry } from "@/lib/activity/types";

/** Milliseconds a running subagent has been going, ticking once a second; null when it is not running or its start is unknown. */
function useElapsed(startedAt: number | undefined, running: boolean): number | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running || startedAt === undefined) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running, startedAt]);
  return running && startedAt !== undefined ? Math.max(0, now - startedAt) : null;
}

/**
 * One subagent, where it was started in the conversation: its assignment's title, how it stands, how long it has been going and
 * what it is doing this moment, taken from its own events, so it needs no request of its own. Opening it shows the assignment,
 * the subagent's steps and its report (`SubagentPanel`), read from the server only while open.
 *
 * Status is always words as well as a glyph. A subagent waiting for the person's answer says so here, because its Approve /
 * Decline card sits at the end of the steps and several subagents can be waiting at once.
 */
export const SubagentCard = memo(function SubagentCard({ taskId, call, animate, waiting }: { taskId: string; call: CallEntry; animate: boolean; waiting: boolean }) {
  const panelId = useId();
  const openable = !!call.toolCallId;
  const [open, toggle] = useDisclosure(disclosureKey(taskId, call.id), false);
  const running = call.outcome === "running";
  const elapsed = useElapsed(call.startedAt, running);
  const time = running ? elapsed : call.durationMs;
  const status = waiting ? "Waiting for your approval" : subagentStatus(call.outcome);
  const standing = [status, time !== null ? formatDuration(time) : null, subagentSteps(call)].filter(Boolean).join(" · ");
  const now = waiting ? null : subagentNow(call);
  const heading = <>
    <Glyph icon={call.icon} tone={call.tone} current={running} />
    <span className="min-w-0 flex-1">
      <span className="block truncate text-sm font-medium leading-5 text-foreground">{call.detail ?? "Subagent"}</span>
      <span className="block truncate text-xs leading-5 text-muted-foreground" data-subagent-standing>{standing}</span>
      {now ? <span className="block truncate text-[13px] leading-5 text-muted-foreground" data-subagent-now><span className="thinking-shimmer">{now}</span></span> : null}
    </span>
    {openable ? chevron(open) : null}
  </>;
  const frame = "flex w-full min-w-0 items-start gap-2.5 rounded-2xl px-3.5 py-3 text-left [&>:first-child]:mt-0.5";
  // A delegation shown while the model was writing it has entered already (see `draftedCalls`).
  const enters = animate && !(call.callId && draftedCalls.has(call.callId));
  return <div className={`${enters ? "message-in " : ""}min-w-0 overflow-hidden rounded-2xl border bg-background shadow-[0_1px_2px_rgb(0_0_0/0.03)] dark:bg-card dark:shadow-none`}
    data-subagent-card data-outcome={call.outcome} data-waiting={waiting ? "true" : undefined}>
    {openable
      ? <button type="button" aria-expanded={open} aria-controls={panelId} data-scroll-anchor-toggle className={`${frame} [@media(pointer:coarse)]:min-h-11 ${toggleClass}`} onClick={toggle}>{heading}</button>
      : <div className={frame}>{heading}</div>}
    {openable ? <div id={panelId} hidden={!open} className="border-t px-3.5 pb-3 pt-2.5">
      {open && call.toolCallId ? <SubagentPanel key={`${taskId}:${call.toolCallId}`} taskId={taskId} toolCallId={call.toolCallId} finished={!running} showHeader={false} /> : null}
    </div> : null}
  </div>;
});

/**
 * The subagents that stand together in the conversation. Two or more get a line saying how many and how they stand, so a team
 * started in one reply reads as a team; one stands alone as its card. `waiting` holds the run ids of subagents that have a
 * question for the person.
 */
export function SubagentFleet({ taskId, calls, animate, waiting }: { taskId: string; calls: readonly CallEntry[]; animate: boolean; waiting: ReadonlySet<string> }) {
  const many = calls.length > 1;
  return <li className="min-w-0 text-foreground" data-activity-entry="subagents" data-count={calls.length}>
    {many ? <p className="mb-2 text-[13px] font-medium leading-5 text-muted-foreground" data-subagent-fleet>{fleetSummary(calls)}</p> : null}
    <ul className="flex min-w-0 flex-col gap-2" aria-label={many ? "Subagents" : "Subagent"}>
      {calls.map((call) => <li key={call.key} className="min-w-0">
        <SubagentCard taskId={taskId} call={call} animate={animate} waiting={!!call.childRunId && waiting.has(call.childRunId)} />
      </li>)}
    </ul>
  </li>;
}
