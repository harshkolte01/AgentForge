"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Download, ExternalLink, RefreshCw } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RunTree } from "@/features/agents/run-tree";
import { ChangeApprovals } from "@/features/github/change-approvals";
import { api, eventTypes, formatDate, message, publicSourceUrl, statusLabel, terminalStatuses,
  type AgentRun, type Artifact, type CodingDetail, type Source, type Task, type TaskEvent, type TaskStep } from "@/lib/task-api";

type Detail = { task: Task; steps: TaskStep[]; runs: AgentRun[]; sources: Source[]; artifacts: Artifact[]; coding: CodingDetail | null };
type ClaimCheck = { claim: string; source_ids: string[]; status: string; reason: string };

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return <section className="min-w-0 space-y-4 border-t pt-6"><h2 className="text-base font-semibold">{title}</h2>{children}</section>;
}

function checksFrom(value: unknown): ClaimCheck[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is ClaimCheck => item !== null && typeof item === "object" &&
    typeof item.claim === "string" && typeof item.status === "string" && typeof item.reason === "string" &&
    Array.isArray(item.source_ids) && item.source_ids.every((id: unknown) => typeof id === "string"));
}
function stringsFrom(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}
function recordFrom(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function TaskDetail({ taskId }: { taskId: string }) {
  const [detail, setDetail] = useState<Detail | null>(null);
  const [events, setEvents] = useState<TaskEvent[]>([]);
  const [connection, setConnection] = useState("Connecting");
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [cancelling, setCancelling] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let cursor = 0;
    let inFlight = false;
    let terminal = false;
    let scheduled: ReturnType<typeof setTimeout> | undefined;
    let stream: EventSource | undefined;

    function append(page: TaskEvent[]) {
      if (controller.signal.aborted || !page.length) return;
      const scoped = page.filter((event) => event.task_id === taskId && Number.isSafeInteger(event.sequence));
      if (!scoped.length) return;
      cursor = Math.max(cursor, ...scoped.map((event) => event.sequence));
      setEvents((current) => [...new Map([...current, ...scoped].map((event) => [event.sequence, event])).values()]
        .sort((a, b) => a.sequence - b.sequence).slice(-200));
    }

    async function catchUp() {
      // Paginate in bounded batches; a busy stream cannot monopolize the browser.
      for (let pageNumber = 0; pageNumber < 10 && !controller.signal.aborted; pageNumber++) {
        const page = await api<TaskEvent[]>(`/tasks/${taskId}/events/history?after=${cursor}&limit=100`, { signal: controller.signal });
        append(page);
        if (page.length < 100) break;
      }
    }

    async function load() {
      if (inFlight || controller.signal.aborted) return;
      inFlight = true;
      try {
        const base = `/tasks/${taskId}`;
        const [task, steps, runs, sources, artifacts] = await Promise.all([
          api<Task>(base, { signal: controller.signal }),
          api<TaskStep[]>(`${base}/steps`, { signal: controller.signal }),
          api<AgentRun[]>(`${base}/runs`, { signal: controller.signal }),
          api<Source[]>(`${base}/sources`, { signal: controller.signal }),
          api<Artifact[]>(`${base}/artifacts`, { signal: controller.signal }),
        ]);
        const coding = task.kind === "coding" ? await api<CodingDetail>(`${base}/coding`, { signal: controller.signal }) : null;
        await catchUp();
        if (!controller.signal.aborted) {
          setDetail({ task, steps, runs, sources, artifacts, coding });
          setError("");
          terminal = terminalStatuses.has(task.status) && cursor >= task.event_sequence;
          if (!stream) setConnection("Polling for updates");
          if (terminal) {
            stream?.close();
            setConnection("Up to date");
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) setError(message(error));
      } finally { inFlight = false; }
    }

    if (typeof EventSource !== "undefined") {
      stream = new EventSource(`/api/v1/tasks/${taskId}/events?after=0`);
      stream.onopen = () => { if (!controller.signal.aborted) setConnection("Live updates"); };
      stream.onerror = () => { if (!controller.signal.aborted && !terminal) setConnection("Reconnecting · polling for updates"); };
      const receive = (raw: Event) => {
        try {
          const event = JSON.parse((raw as MessageEvent<string>).data) as TaskEvent;
          append([event]);
          if (!scheduled) scheduled = setTimeout(() => { scheduled = undefined; void load(); }, 300);
        } catch { setConnection("Reconnecting · polling for updates"); }
      };
      for (const type of eventTypes) stream.addEventListener(type, receive);
      stream.addEventListener("session.closed", () => {
        stream?.close();
        window.dispatchEvent(new Event("agentforge-session-expired"));
      });
    }
    void load();
    const polling = window.setInterval(() => { if (!terminal) void load(); }, 5000);
    return () => {
      controller.abort(); stream?.close(); window.clearInterval(polling);
      if (scheduled) clearTimeout(scheduled);
    };
  }, [taskId, refresh]);

  if (!detail) {
    return <div className="space-y-6">{error ? <div role="alert" className="space-y-4"><h1 className="text-2xl font-semibold">Task unavailable</h1><p className="text-sm text-destructive">{error}</p><Button variant="outline" onClick={() => setRefresh((value) => value + 1)}>Retry</Button></div> : <div aria-label="Loading task" className="space-y-6"><div className="h-9 w-2/3 rounded-lg bg-muted" /><div className="h-24 rounded-lg bg-muted" /><div className="h-64 rounded-lg bg-muted" /></div>}</div>;
  }
  const { task, steps, runs, sources, artifacts, coding } = detail;
  const finished = terminalStatuses.has(task.status);
  const verification = recordFrom(task.final_result?.verification);
  const checks = checksFrom(verification.checks);
  const limitations = stringsFrom(verification.limitations);

  return (
    <div className="space-y-9">
      <header className="space-y-5">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-3"><Badge variant={task.status === "FAILED" ? "destructive" : "secondary"}>{statusLabel(task.status)}</Badge><span className="text-xs text-muted-foreground" role="status">{connection}</span></div>
          <div className="flex flex-wrap gap-2"><Button variant="outline" size="sm" onClick={() => setRefresh((value) => value + 1)}><RefreshCw aria-hidden="true" />Refresh</Button>{!finished ? <Button variant="destructive" size="sm" disabled={cancelling || !!task.cancel_requested_at} onClick={async () => {
            setCancelling(true); setActionError("");
            try {
              const updated = await api<Task>(`/tasks/${taskId}/cancel`, { method: "POST" });
              setDetail((current) => current ? { ...current, task: updated } : current);
              setRefresh((value) => value + 1);
            } catch (error) { setActionError(message(error)); }
            finally { setCancelling(false); }
          }}>{task.cancel_requested_at ? "Cancellation requested" : cancelling ? "Requesting…" : "Cancel task"}</Button> : null}</div>
        </div>
        <h1 className="max-w-4xl whitespace-pre-wrap break-words text-2xl font-semibold leading-snug tracking-tight">{task.goal}</h1>
        <p className="text-xs leading-relaxed text-muted-foreground">Created {formatDate(task.created_at)} · ${task.max_cost_usd} cost limit · {task.max_wall_time_sec}s time limit</p>
        {task.status === "CREATED" ? <p className="max-w-prose text-sm leading-relaxed text-muted-foreground">Queued for the worker. If this does not change, check the worker process and its configuration. This page does not confirm worker liveness.</p> : null}
        {task.cancel_requested_at && !finished ? <p className="text-sm text-muted-foreground">Cancellation is cooperative. An in-flight operation may finish before the worker stops.</p> : null}
        {error || actionError ? <p role="alert" className="text-sm text-destructive">{actionError || error}{error ? " Displaying the last received task state." : ""}</p> : null}
      </header>

      <div className="grid items-start gap-10 xl:grid-cols-[minmax(0,1.6fr)_minmax(18rem,1fr)]">
        <div className="min-w-0 space-y-8">
          <Panel title={task.kind === "coding" ? "Code change outcome" : "Task outcome"}>
            {task.final_summary ? <p className="max-w-prose whitespace-pre-wrap break-words text-sm leading-7">{task.final_summary}</p> : <p className="text-sm leading-relaxed text-muted-foreground">{finished ? task.kind === "coding" ? "This task ended without a final code change summary. Review the execution evidence and timeline." : "This task ended without a final research summary. Review the timeline and any sources collected below." : task.kind === "coding" ? "The final summary will appear after code review and sandbox verification." : "The final summary will appear here after the worker verifies the research."}</p>}
            {task.error_code ? <p className="break-words text-sm text-destructive">Worker reported: <code>{task.error_code}</code></p> : null}
            {artifacts.length ? <div className="flex flex-col items-start gap-3">{artifacts.map((artifact) => <a key={artifact.id} href={`/api/v1/tasks/${taskId}/artifacts/${artifact.id}`} className="inline-flex items-center gap-2 text-sm font-medium underline underline-offset-4 focus-visible:outline-2"><Download aria-hidden="true" className="size-4" />Download {artifact.filename}<span className="font-normal text-muted-foreground">({Math.ceil(artifact.byte_size / 1024)} KB)</span></a>)}</div> : null}
            {checks.length ? <div className="space-y-4"><h3 className="text-sm font-medium">Claim checks</h3>{checks.map((check, index) => <div key={`${index}-${check.claim}`} className="space-y-2 rounded-xl border p-4"><Badge variant={check.status === "QUOTE_SUPPORTED" ? "secondary" : "outline"}>{statusLabel(check.status)}</Badge><p className="break-words text-sm leading-relaxed">{check.claim}</p><p className="text-xs leading-relaxed text-muted-foreground">{check.reason}</p>{check.source_ids.length ? <div className="flex flex-wrap gap-x-4 gap-y-2">{check.source_ids.map((sourceId, sourceIndex) => <a key={`${sourceId}-${sourceIndex}`} href={`#source-${sourceId}`} className="text-xs underline underline-offset-4 focus-visible:outline-2">Source {sources.findIndex((source) => source.id === sourceId) >= 0 ? sources.findIndex((source) => source.id === sourceId) + 1 : sourceId.slice(0, 8)}</a>)}</div> : null}</div>)}</div> : null}
            {limitations.length ? <div><h3 className="text-sm font-medium">Limitations</h3><ul className="mt-2 list-disc space-y-2 pl-5 text-sm leading-relaxed text-muted-foreground">{limitations.map((limitation, index) => <li key={`${index}-${limitation}`}>{limitation}</li>)}</ul></div> : null}
          </Panel>

          {task.kind === "coding" ? <CodingEvidence detail={coding} spec={task.coding} finished={finished} /> : <Panel title="Sources">
            {!sources.length ? <p className="text-sm leading-relaxed text-muted-foreground">{finished ? "No research sources were persisted for this task." : "Source records appear here as agents search and retrieve evidence."}</p> : <ol className="divide-y">{sources.map((source, index) => {
              const href = publicSourceUrl(source.url);
              return <li key={source.id} id={`source-${source.id}`} className="scroll-mt-8 space-y-3 py-5 first:pt-0"><div className="flex flex-wrap items-center gap-2"><span className="text-xs text-muted-foreground">Source {index + 1}</span><Badge variant={source.fetch_status === "FAILED" ? "destructive" : "outline"}>{statusLabel(source.fetch_status)}</Badge></div>{href ? <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex max-w-full items-start gap-2 text-sm font-medium underline underline-offset-4 focus-visible:outline-2"><span className="break-words">{source.title || source.url}</span><ExternalLink aria-hidden="true" className="mt-0.5 size-3 shrink-0" /></a> : <p className="break-words text-sm font-medium">{source.title || "Source"}</p>}<p className="break-all text-xs text-muted-foreground">{source.url}</p>{source.excerpt ? <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-muted-foreground">{source.excerpt}</p> : null}<p className="text-xs text-muted-foreground">Retrieved {formatDate(source.retrieved_at)}{source.fetch_status === "SEARCH_ONLY" ? " · Search preview; page not fetched" : ""}</p>{source.error_code ? <p className="text-xs text-destructive">{source.error_code}</p> : null}</li>;
            })}</ol>}
          </Panel>}
        </div>

        <aside className="min-w-0 space-y-8" aria-label="Execution details">
          {task.kind === "coding" && finished ? <ChangeApprovals taskId={taskId} /> : null}
          <Panel title="Plan">
            {!steps.length ? <p className="text-sm leading-relaxed text-muted-foreground">{finished ? "No plan was persisted." : "Waiting for the worker to create a plan."}</p> : <ol className="space-y-5">{steps.map((step) => <li key={step.id} className="space-y-2"><div className="flex flex-wrap items-center gap-2"><Badge variant="outline">{statusLabel(step.status)}</Badge><span className="text-xs text-muted-foreground">{step.agent_type} v{step.definition_version}</span></div><p className="break-words text-sm font-medium leading-relaxed">{step.objective}</p>{step.acceptance_criteria.length ? <ul className="list-disc space-y-1 pl-4 text-xs leading-relaxed text-muted-foreground">{step.acceptance_criteria.map((criterion, index) => <li key={`${step.id}-${index}`}>{criterion}</li>)}</ul> : null}{step.depends_on.length ? <p className="text-xs text-muted-foreground">After: {step.depends_on.join(", ")}</p> : null}</li>)}</ol>}
          </Panel>
          <RunTree runs={runs} steps={steps} />
          <Panel title="Timeline">
            <p className="text-xs text-muted-foreground">Server events only. Showing up to the latest 200 received events.</p>
            {!events.length ? <p className="text-sm text-muted-foreground">Waiting for persisted events.</p> : <ol className="space-y-4">{events.map((event) => <li key={event.sequence} className="space-y-1"><div className="flex items-baseline justify-between gap-2"><p className="break-words text-sm font-medium">{event.event_type.split(".").map(statusLabel).join(" · ")}</p><span className="text-xs tabular-nums text-muted-foreground">#{event.sequence}</span></div><time className="block text-xs text-muted-foreground" dateTime={event.created_at}>{formatDate(event.created_at)}</time>{typeof event.payload.tool === "string" ? <p className="text-xs text-muted-foreground">{event.payload.tool}</p> : null}{typeof event.payload.model === "string" ? <p className="text-xs text-muted-foreground">{event.payload.model}</p> : null}{typeof event.payload.status === "string" ? <p className="text-xs">{statusLabel(event.payload.status)}</p> : null}{typeof event.payload.error_code === "string" ? <p className="break-words text-xs text-destructive">{event.payload.error_code}</p> : null}</li>)}</ol>}
          </Panel>
        </aside>
      </div>
    </div>
  );
}


function CodingEvidence({ detail, spec, finished }: { detail: CodingDetail | null; spec: Task["coding"]; finished: boolean }) {
  const repository = detail?.session?.repo_id ?? spec?.repository.repo_id;
  const commit = detail?.session?.commit_sha ?? spec?.repository.ref;
  return <Panel title="Code and execution evidence">
    {repository ? <div className="space-y-2 text-xs text-muted-foreground"><p className="break-all">Repository: {repository}</p><p className="break-all">Pinned commit: <code>{commit}</code></p>{detail?.session ? <p className="break-all">Workspace revision: <code>{detail.session.revision}</code></p> : <p>{finished ? "No workspace was persisted." : "Waiting for the worker to prepare the workspace."}</p>}</div> : null}
    <p className="text-xs leading-relaxed text-muted-foreground">Test logs show the observed sandbox result. Code review is model evidence; it does not replace running tests or reviewing the downloaded patch.</p>
    {!detail?.evidence.length ? <p className="text-sm text-muted-foreground">{finished ? "No coding evidence was persisted for this task." : "Diffs, test logs, and review evidence appear as the worker persists them."}</p> : <ol className="space-y-6">{detail.evidence.map((evidence) => <li key={evidence.id} className="min-w-0 space-y-3 border-t pt-4">
      <div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-medium">{statusLabel(evidence.kind)}</h3>{typeof evidence.status === "string" ? <Badge variant="outline">{statusLabel(evidence.status)}</Badge> : null}{typeof evidence.exit_code === "number" ? <Badge variant={evidence.exit_code === 0 ? "secondary" : "destructive"}>Exit {evidence.exit_code}</Badge> : null}</div>
      <p className="break-all text-xs text-muted-foreground">Revision <code>{evidence.revision}</code></p>
      {typeof evidence.profile_id === "string" ? <p className="text-xs">Profile: <code>{evidence.profile_id}</code></p> : null}
      {typeof evidence.summary === "string" ? <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{evidence.summary}</p> : null}
      {(["diff", "stdout", "stderr", "stdout_preview", "stderr_preview", "output", "log"] as const).map((key) => typeof evidence[key] === "string" && evidence[key] ? <div key={key} className="min-w-0 space-y-2"><h4 className="text-xs font-medium">{statusLabel(key)}</h4><pre className="max-h-96 overflow-auto rounded-lg bg-muted/50 p-4 text-xs leading-6" tabIndex={0} aria-label={statusLabel(key)}>{evidence[key] as string}</pre></div> : null)}
      {evidence.output_truncated === true || evidence.logs_preview === true ? <p className="text-xs text-muted-foreground">Logs shown here are bounded previews. Download the execution log for the persisted output and truncation details.</p> : null}
      {evidence.qualified === false ? <p className="text-sm text-muted-foreground">This result does not qualify as passing verification.{typeof evidence.qualification_limit === "string" ? ` ${evidence.qualification_limit}` : ""}</p> : null}
      <details className="min-w-0 text-xs"><summary className="cursor-pointer font-medium focus-visible:outline-2">Evidence record</summary><pre className="mt-3 max-h-96 overflow-auto rounded-lg bg-muted/50 p-4 leading-6" tabIndex={0} aria-label="Evidence record">{JSON.stringify(evidence, null, 2)}</pre></details>
    </li>)}</ol>}
  </Panel>;
}
