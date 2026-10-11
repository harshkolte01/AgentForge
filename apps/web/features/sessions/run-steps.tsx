"use client";
import { SubagentFleet } from "@/features/sessions/subagent-card";
import { useDisclosure } from "@/features/sessions/use-disclosure";
import { memo, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { TriangleAlert } from "lucide-react";
import { ApprovalCard } from "@/features/sessions/approval-card";
import { AssistantMessage } from "@/features/sessions/assistant-message";
import { loadHistoryEntries } from "@/features/sessions/history-activity";
import { Glyph, chevron, toggleClass } from "@/features/sessions/row-parts";
import { ToolDetailPanel } from "@/features/sessions/tool-detail";
import {
  activityLabel, approvalSource, chunkRows, currentCallId, placeLiveTool, sectionsOf, subagentsByRun, summarizeEntries, type Section, type TimelineNotice,
} from "@/features/sessions/timeline-model";
import type { PendingApproval } from "@/lib/activity/approvals";
import { ACTIVITY_DISCLOSURE, disclosureKey } from "@/lib/activity/disclosure";
import type { LiveToolView } from "@/lib/activity/live-text";
import { describeDraft } from "@/lib/activity/presentation";
import type { ActivityEntry, CallEntry, GroupEntry, MessageActivity, MilestoneEntry, PhaseEntry, StatusIcon, Tone } from "@/lib/activity/types";
import { fetchEventHistory } from "@/lib/task-api";

// ---------------------------------------------------------------------------------------------------------------------
// Rows

function Words({ label, detail, shimmer }: { label: string; detail?: string; shimmer: boolean }) {
  return <span className="min-w-0 truncate">
    {shimmer ? <span className="thinking-shimmer">{label}</span> : label}
    {detail ? <span className="ml-1.5 opacity-70">{detail}</span> : null}
  </span>;
}

/**
 * Text a call is writing or printing right now: the command or file content a model is still writing, or the end of what a
 * running command has printed. Bounded, monospace, with its own scroll area that follows the newest text until the reader
 * scrolls up to read, and quiet for a screen reader (the log is `aria-live="off"`; this is a region to move into, not news).
 */
function LiveText({ text, label }: { text: string; label: string }) {
  const box = useRef<HTMLPreElement>(null);
  const following = useRef(true);
  useLayoutEffect(() => {
    const element = box.current;
    if (element && following.current) element.scrollTop = element.scrollHeight;
  }, [text]);
  if (!text) return null;
  return <pre ref={box} tabIndex={0} role="region" aria-label={label} data-live-text
    onScroll={(event) => { const element = event.currentTarget; following.current = element.scrollHeight - element.scrollTop - element.clientHeight < 24; }}
    className="mt-2 max-h-48 max-w-full overflow-auto overscroll-contain whitespace-pre rounded-lg border bg-muted/40 p-3 font-mono text-xs leading-5 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/60">{text}</pre>;
}

/** A call the model is still writing: a row of its own (nothing runs yet, so it never says "running") with what it has so far. */
const DraftRow = memo(function DraftRow({ call }: { call: LiveToolView }) {
  const shown = describeDraft(call.name, call.detail);
  if (!shown) return null;
  return <li className="message-in min-w-0" data-activity-entry="draft" data-tool={call.name}>
    <div className="flex items-center gap-2.5">
      <Glyph icon="running" tone="neutral" current />
      <Words label={shown.label} detail={shown.detail} shimmer />
    </div>
    <LiveText text={call.text} label={shown.label} />
  </li>;
});

type RowEntry = MilestoneEntry | CallEntry | GroupEntry;

const rowButton = `group/row -my-px flex min-h-6 w-full min-w-0 items-center gap-2.5 rounded-md text-left [@media(pointer:coarse)]:min-h-11 ${toggleClass}`;

/**
 * One line of the timeline: a step, a counted group of steps, or a milestone. Memoized on the entry's identity, so a streaming
 * update re-renders only the rows that changed. Only the newest running step animates; other running steps keep a calm dot.
 * A group folds into one counted line whose disclosure lists every call, and an execution, diff or artifact call opens its
 * details; both remember a manual choice.
 */
export const EntryRow = memo(function EntryRow({ taskId, entry, current, animate, output }: { taskId: string; entry: RowEntry; current: boolean; animate: boolean; output?: string }) {
  const panelId = useId();
  const expandable = entry.kind === "group" || (entry.kind === "call" && entry.detailable);
  const [open, toggle] = useDisclosure(disclosureKey(taskId, entry.id), false);
  const base = `${animate ? "message-in " : ""}min-w-0`;
  const meta = entry.kind === "milestone"
    ? { icon: "success" as StatusIcon, tone: "success" as Tone, label: entry.label, detail: undefined, outcome: "completed" }
    : { icon: entry.icon, tone: entry.tone, label: entry.label, detail: entry.kind === "call" ? entry.detail : undefined, outcome: entry.outcome };
  const shimmer = current && entry.kind === "call" && entry.outcome === "running";
  const hooks = { "data-activity-entry": entry.kind, "data-outcome": meta.outcome, "data-tone": meta.tone, ...(entry.kind !== "milestone" ? { "data-tool": entry.tool } : {}) };
  if (!expandable) {
    // A running command shows what it has printed so far under its row; the finished row opens the saved output instead.
    return <li className={base} {...hooks}>
      <div className="flex items-center gap-2.5">
        <Glyph icon={meta.icon} tone={meta.tone} current={current} />
        <Words label={meta.label} detail={meta.detail} shimmer={shimmer} />
      </div>
      {output ? <LiveText text={output} label="Live output" /> : null}
    </li>;
  }
  return <li className={base} {...hooks}>
    <button type="button" aria-expanded={open} aria-controls={panelId} data-scroll-anchor-toggle className={rowButton} onClick={toggle}>
      <Glyph icon={meta.icon} tone={meta.tone} current={current} />
      <Words label={meta.label} detail={meta.detail} shimmer={shimmer} />
      {chevron(open)}
    </button>
    <div id={panelId} hidden={!open} className="mt-2 ml-2 border-l pl-4">
      {open ? (entry.kind === "group"
        ? <ul className="space-y-2" aria-label="Actions in this group">{entry.calls.map((call) => <EntryRow key={call.key} taskId={taskId} entry={call} current={false} animate={false} />)}</ul>
        : entry.kind === "call" && entry.toolCallId ? <ToolDetailPanel key={`${taskId}:${entry.toolCallId}`} taskId={taskId} toolCallId={entry.toolCallId} /> : null) : null}
    </div>
  </li>;
});

const PhaseHeading = memo(function PhaseHeading({ entry }: { entry: PhaseEntry }) {
  return <p className="mt-1 flex items-center gap-1.5 text-[13px] font-medium text-muted-foreground" data-activity-entry="phase" data-state={entry.state}>
    <span>{entry.label}</span>
    {entry.state === "failed" ? <><TriangleAlert className="size-3" aria-hidden="true" /><span className="font-normal">didn&apos;t finish</span></> : null}
  </p>;
});

const NoteBlock = memo(function NoteBlock({ text }: { text: string }) {
  return <div className="min-w-0" data-activity-entry="note"><AssistantMessage content={text} /></div>;
});

/**
 * A line about the connection or the run itself (stopping, thinking): the same row shape as a step. It plays the entrance only
 * when it is genuinely new: "Thinking" moves from the just-sent indicator into the run's own view as a fresh element, and
 * replaying the fade there would flicker.
 */
function StatusRow({ icon = "running", current, label, children, animate = false, fade = false }: { icon?: StatusIcon; current: boolean; label: string; children?: ReactNode; animate?: boolean; fade?: boolean }) {
  // Work in progress ("Thinking", "Stopping") is the shimmering words alone; only a warning keeps a glyph to mark it apart.
  // `fade`: a line that comes back during a run fades in slowly, so one that the run's end removes a moment later never flashes.
  return <li className={`${animate ? "message-in " : fade ? "status-fade " : ""}flex min-w-0 items-center gap-2.5`} data-activity-entry="status">
    {icon === "running" ? null : <Glyph icon={icon} tone="neutral" current={current} />}
    <span className="min-w-0 truncate">{current && icon === "running" ? <span className="thinking-shimmer">{label}</span> : label}</span>
    {children}
  </li>;
}

// ---------------------------------------------------------------------------------------------------------------------
// Timeline

type TimelineProps = {
  /** The task the entries belong to: disclosure choices are remembered per task. */
  taskId?: string;
  entries: readonly ActivityEntry[];
  /** The run is still going: a status line ("Thinking") may show. */
  live?: boolean;
  /** Nothing new has shown for a while. */
  quiet?: boolean;
  fallback?: string;
  /** Text still being written (a progress note, then the answer). */
  tail?: ReactNode;
  /** Trouble reaching the server, shown inside the timeline. */
  notice?: TimelineNotice | null;
  onRetry?: () => void;
  stopping?: boolean;
  /** Rows play their entrance. Off for activity that is loaded or was already shown, so nothing replays. */
  animate?: boolean;
  /** The one short sentence a screen reader hears (never the streamed words). Omitted: no status region. */
  announcement?: string;
  /** The run is over: its steps fold into the closed "Worked · 41s · N steps" disclosure, the same one the saved answer shows. */
  fold?: boolean;
  durationMs?: number | null;
  /** Calls the agent stopped before, waiting for an Approve / Decline. */
  approvals?: readonly PendingApproval[];
  /** The "Thinking" line plays its entrance (only the indicator shown the moment a message is sent). */
  statusEnters?: boolean;
  /** The tool call the live row shows: being written by the model, or running and printing. */
  liveTool?: LiveToolView | null;
};

function renderSection(section: Section, taskId: string, current: string | null, animate: boolean, extra: ReactNode, outputFor: { entryId: string; text: string } | null, waiting: ReadonlySet<string>) {
  switch (section.kind) {
    case "note": return <NoteBlock key={section.id} text={section.entry.text} />;
    case "phase": return <PhaseHeading key={section.id} entry={section.entry} />;
    case "rows": return <ul key={section.id} className="space-y-2 text-[15px] leading-6 text-muted-foreground" aria-label="Steps">
      {chunkRows(section.entries).map((chunk) => chunk.kind === "subagents"
        ? <SubagentFleet key={chunk.key} taskId={taskId} calls={chunk.calls} animate={animate} waiting={waiting} />
        : <EntryRow key={chunk.key} taskId={taskId} entry={chunk.entry} current={chunk.entry.id === current} animate={animate} output={outputFor?.entryId === chunk.entry.id ? outputFor.text : undefined} />)}
      {extra}
    </ul>;
  }
}

/**
 * What the agent said and did, oldest first, as it happened: its notes in the person's own reading voice and one line per step.
 * While `live`, one status line ("Thinking") appears only when nothing else shows that work is going on: no step is running and
 * no text is being written (`tail`). Straight after the start of a run that is immediately; once there is something to read,
 * only when the run has been `quiet` for a while, so it never competes with text the agent has just written.
 *
 * The log itself is `aria-live="off"`: streaming text and growing rows would be read out word by word. A separate polite status
 * says only what changed in the run (`announcement`).
 */
export function Timeline({ taskId = "", entries, live = false, quiet = false, fallback = "Thinking", tail, notice = null, onRetry, stopping = false, animate = true, announcement, fold = false, durationMs, approvals = [], statusEnters = false, liveTool = null }: TimelineProps) {
  const sections = useMemo(() => sectionsOf(entries), [entries]);
  const callId = useMemo(() => currentCallId(entries), [entries]);
  // Subagents that have a question for the person, and whose each question is.
  const subagents = useMemo(() => subagentsByRun(entries), [entries]);
  const waiting = useMemo(() => new Set(approvals.flatMap((approval) => approval.agentRunId ? [approval.agentRunId] : [])), [approvals]);
  const nothingYet = !entries.some((entry) => entry.kind !== "phase");
  // A call being written, or a running command's output. Not shown once the run is folded away into its summary.
  const placement = useMemo(() => fold ? null : placeLiveTool(entries, liveTool, approvals.length > 0), [fold, entries, liveTool, approvals.length]);
  const draftRow = placement?.kind === "draft" ? <DraftRow key={`draft:${placement.call.callId}`} call={placement.call} /> : null;
  const outputFor = placement?.kind === "output" ? placement : null;
  // A call being written is work in progress too: "Thinking" would say nothing is happening while its text is arriving.
  const running = callId !== null || stopping || !!draftRow;
  // A connection notice stands in for "Thinking" when nothing else shows life; otherwise it sits quietly under the rest.
  // A waiting approval card is the sign of life: "Thinking" would say the agent is busy while it waits for the person.
  const status = live && !tail && !running && !notice && !approvals.length && (nothingYet || quiet);
  const noticeLeads = !!notice && !tail && !running && notice.kind !== "halted" && notice.kind !== "failed";
  const current = stopping ? "stopping" : callId;
  const last = sections[sections.length - 1];
  const stoppingRow = stopping ? <StatusRow key="stopping" current label="Stopping" animate /> : null;
  // What follows the last list of steps: the call being written, then "Stopping". Each opens a list of its own after a note or heading.
  const trailing = draftRow || stoppingRow ? <>{draftRow}{stoppingRow}</> : null;
  return <>
    <div className="flex min-w-0 flex-col gap-3" role="log" aria-live="off" aria-label="Agent activity" data-activity-log data-live={live ? "true" : undefined}>
      {fold ? <ActivityDisclosure taskId={taskId} entries={entries} durationMs={durationMs} className="" />
        : sections.map((section, index) => renderSection(section, taskId, current, animate, section.kind === "rows" && index === sections.length - 1 ? trailing : null, outputFor, waiting))}
      {trailing && last?.kind !== "rows" ? <ul className="space-y-2 text-[15px] leading-6 text-muted-foreground" aria-label="Steps">{trailing}</ul> : null}
      {approvals.map((approval) => <ApprovalCard key={approval.id} approval={approval} source={approvalSource(approval.agentRunId, subagents)} />)}
      {tail}
      {notice ? <ul className="text-[15px] leading-6 text-muted-foreground" aria-label="Connection" data-activity-notice={notice.kind}>
        <StatusRow icon={noticeLeads ? "running" : notice.kind === "reconnecting" || notice.kind === "catching_up" ? "unknown" : "warning"} current={noticeLeads} label={notice.text} animate>
          {notice.kind === "failed" && onRetry ? <button type="button" onClick={onRetry} className={`ml-1 shrink-0 rounded px-1 font-medium text-foreground underline underline-offset-4 [@media(pointer:coarse)]:min-h-11 ${toggleClass}`}>Retry</button> : null}
        </StatusRow>
      </ul> : null}
      {status ? <ul className="text-[15px] leading-6 text-muted-foreground" aria-label="Status"><StatusRow current label={fallback} animate={statusEnters} fade={!statusEnters && !nothingYet} /></ul> : null}
    </div>
    {announcement !== undefined ? <span role="status" aria-live="polite" className="sr-only" data-activity-status>{announcement}</span> : null}
  </>;
}

// ---------------------------------------------------------------------------------------------------------------------
// Disclosure of a finished run's activity

/** Entries read from the server for finished replies, kept for the page session so reopening is instant and costs no request. */
const history = new Map<string, readonly ActivityEntry[]>();
const HISTORY_LIMIT = 40;

function useHistoryEntries(taskId: string, terminal: boolean, enabled: boolean) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; entries?: readonly ActivityEntry[]; error?: string } | null>(null);
  const key = `${taskId}:${attempt}`;
  const cached = history.get(taskId);
  useEffect(() => {
    if (!enabled || cached) return;
    const controller = new AbortController();
    loadHistoryEntries(taskId, { terminal }, (after, limit, signal) => fetchEventHistory(taskId, after, limit, { signal }), controller.signal).then(
      (entries) => {
        history.set(taskId, entries);
        while (history.size > HISTORY_LIMIT) history.delete(history.keys().next().value as string);
        if (!controller.signal.aborted) setResult({ key, entries });
      },
      () => { if (!controller.signal.aborted) setResult({ key, error: "Couldn’t load this reply’s activity." }); },
    );
    return () => controller.abort();
  }, [enabled, cached, taskId, terminal, key]);
  const entries = cached ?? (result?.key === key ? result.entries : undefined);
  const error = entries ? undefined : result?.key === key ? result.error : undefined;
  return { entries, error, loading: enabled && !entries && !error, retry: () => setAttempt((value) => value + 1) };
}

/**
 * Collapsible summary of a run's work. Given `entries` (a run that just ended in this view) it shows them, closed unless the person opened it.
 * Given only a `taskId` and the message's `activity` (history) it is collapsed, labelled from the exact action count, and loads
 * the whole timeline the first time it opens. Without `activity` (an older server) `legacyCount` stands in for the count.
 */
export function ActivityDisclosure({ taskId, activity, entries, durationMs, legacyCount = 0, className = "mb-3" }: {
  taskId: string;
  activity?: MessageActivity | null;
  entries?: readonly ActivityEntry[];
  /** How long the run took, when known; history reads it from `activity.duration_ms` instead. */
  durationMs?: number | null;
  legacyCount?: number;
  className?: string;
}) {
  const [open, toggle] = useDisclosure(disclosureKey(taskId, ACTIVITY_DISCLOSURE), false);
  const panelId = useId();
  const counts = useMemo(() => entries ? summarizeEntries(entries) : null, [entries]);
  const actions = counts ? counts.actions : activity ? activity.action_count : legacyCount;
  const notes = counts ? counts.notes : activity?.note_count ?? 0;
  const available = counts ? actions > 0 || notes > 0 : activity ? activity.available : legacyCount > 0;
  const loaded = useHistoryEntries(taskId, activity?.terminal ?? true, open && available && !entries);
  if (!available) return null;
  const shown = entries ?? loaded.entries;
  return <div className={className} data-activity-disclosure>
    <button type="button" aria-expanded={open} aria-controls={panelId} data-scroll-anchor-toggle className={`-ml-1 inline-flex min-h-8 items-center gap-1 rounded-lg px-1 text-[13px] text-muted-foreground [@media(pointer:coarse)]:min-h-11 ${toggleClass}`} onClick={toggle}>
      {chevron(open)}
      {activityLabel(actions, durationMs ?? activity?.duration_ms)}
    </button>
    <div id={panelId} hidden={!open} className="mt-1.5 ml-1.5 border-l pl-4 text-sm">
      {open ? (loaded.error ? <p role="alert" className="text-muted-foreground">{loaded.error} <button type="button" onClick={loaded.retry} className={`rounded px-1 font-medium text-foreground underline underline-offset-4 [@media(pointer:coarse)]:min-h-11 ${toggleClass}`}>Retry</button></p>
        : shown ? (shown.length ? <Timeline taskId={taskId} entries={shown} animate={false} /> : <p className="text-muted-foreground">No recorded activity.</p>)
          : <p role="status" className="text-muted-foreground">Loading…</p>) : null}
    </div>
  </div>;
}
