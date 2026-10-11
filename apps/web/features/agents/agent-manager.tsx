"use client";
import { useEffect, useState, type FormEvent } from "react";
import { ArrowLeft, Bot, ChevronRight, FlaskConical, Plus, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { AgentEditor } from "@/features/agents/agent-editor";
import { toolLabel } from "@/features/agents/agent-tools";
import { cardClass, chipClass, EmptyState, Fact, Facts, Group, iconTileClass, SectionHeader } from "@/features/settings/settings-ui";
import { inputClass } from "@/components/session-boundary";
import { api, formatDate, message, type Task } from "@/lib/task-api";
import type { AgentVersion, CustomAgent } from "@/lib/agents-api";

/** The tools an agent was given, not counting the task inputs every agent can read. */
function toolsOf(agent: CustomAgent) { return agent.definition.allowed_tools.filter((tool) => tool !== "read_task_input"); }

export function AgentManager({ projectId, initialCreate = false, initialAgentId, embedded = false, onChanged }: { initialCreate?: boolean; initialAgentId?: string; embedded?: boolean; projectId?: string; onChanged?: () => void }) {
  const [agents, setAgents] = useState<CustomAgent[]>([]);
  const [selected, setSelected] = useState<CustomAgent | null>(null);
  const [versions, setVersions] = useState<AgentVersion[]>([]);
  const [editing, setEditing] = useState(initialCreate);
  const [testGoal, setTestGoal] = useState("");
  const [testTask, setTestTask] = useState<Task | null>(null);
  const [testVersion, setTestVersion] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const selectedId = selected?.id;
  useEffect(() => {
    const controller = new AbortController();
    void api<CustomAgent[]>(`/agents?limit=50${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ""}`, { signal: controller.signal }).then((data) => {
      if (!controller.signal.aborted) { setAgents(data); setHasMore(data.length === 50); setLoading(false); setError(""); }
    }).catch((error) => { if (!controller.signal.aborted) { setError(message(error)); setLoading(false); } });
    return () => controller.abort();
  }, [projectId, refresh]);
  useEffect(() => {
    if (!initialAgentId) return;
    const controller = new AbortController();
    void api<CustomAgent>(`/agents/${encodeURIComponent(initialAgentId)}`, { signal: controller.signal }).then((agent) => {
      if (!controller.signal.aborted) { setSelected(agent); setTestVersion(agent.current_version); setEditing(false); }
    }).catch((error) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => controller.abort();
  }, [initialAgentId]);
  useEffect(() => {
    if (!selectedId || !refresh) return;
    const controller = new AbortController();
    void api<CustomAgent>(`/agents/${selectedId}`, { signal: controller.signal }).then((agent) => {
      if (!controller.signal.aborted) { setSelected(agent); setTestVersion(agent.current_version); }
    }).catch((error) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => controller.abort();
  }, [selectedId, refresh]);
  async function loadMore() {
    setLoadingMore(true); setError("");
    try {
      const data = await api<CustomAgent[]>(`/agents?limit=50&offset=${agents.length}${projectId ? `&project_id=${encodeURIComponent(projectId)}` : ""}`);
      setAgents((current) => [...current, ...data]); setHasMore(data.length === 50);
    } catch (error) { setError(message(error)); } finally { setLoadingMore(false); }
  }
  useEffect(() => {
    if (!selected) return;
    const controller = new AbortController();
    void api<AgentVersion[]>(`/agents/${selected.id}/versions`, { signal: controller.signal }).then((data) => {
      if (!controller.signal.aborted) setVersions(data);
    }).catch((error) => { if (!controller.signal.aborted) setError(message(error)); });
    return () => controller.abort();
  }, [selected]);
  function choose(agent: CustomAgent | null, edit = false) { setSelected(agent); setVersions([]); setEditing(edit); setTestVersion(agent?.current_version ?? 0); setTestTask(null); setError(""); }
  async function test(event: FormEvent) {
    event.preventDefault(); if (!selected || busy) return;
    setBusy(true); setError("");
    try { setTestTask(await api<Task>(`/agents/${selected.id}/test`, { method: "POST", body: JSON.stringify({ version: testVersion, goal: testGoal, allowed_web_hosts: (versions.find((item) => item.version === testVersion)?.definition ?? selected.definition).permissions.allowed_web_hosts ?? [] }) })); }
    catch (error) { setError(message(error)); } finally { setBusy(false); }
  }
  async function disable() {
    if (!selected) return;
    setBusy(true); setError("");
    try { await api<void>(`/agents/${selected.id}`, { method: "DELETE" }); choose(null); setRefresh((value) => value + 1); onChanged?.(); }
    catch (error) { setError(message(error)); } finally { setBusy(false); }
  }
  const backButton = (label: string, onClick: () => void) => <Button variant="ghost" size="sm" className="-ml-2.5 text-muted-foreground hover:text-foreground" disabled={busy} onClick={onClick}><ArrowLeft />{label}</Button>;
  const labsBadge = <span className="rounded-full border px-1.5 text-[10px] font-medium uppercase leading-4 tracking-wide text-muted-foreground">Labs</span>;
  return <section className={embedded ? "space-y-8" : "mx-auto w-full max-w-5xl space-y-8 px-5 py-9 sm:px-8"} aria-labelledby="agents-title">
    <SectionHeader id="agents-title" title={<span className="flex items-center gap-2.5">Custom agents{labsBadge}</span>} description="Create specialized assistants and manage their instructions."
      action={<div className="flex shrink-0 gap-2"><Button variant="ghost" size="icon" aria-label="Refresh custom agents" disabled={busy || loadingMore || loading} onClick={() => { setLoading(true); setRefresh((value) => value + 1); }}><RefreshCw className={loading ? "animate-spin motion-reduce:animate-none" : ""} /></Button><Button disabled={busy} onClick={() => choose(null, true)}><Plus />New agent</Button></div>} />
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    {editing ? <div className="space-y-6">
      {backButton(selected ? `Back to ${selected.name}` : "All agents", () => selected ? setEditing(false) : choose(null))}
      <AgentEditor key={`${selected?.id ?? "new"}-${selected?.current_version ?? 0}`} projectId={selected?.project_id ?? projectId} existing={selected} onCancel={() => selected ? setEditing(false) : choose(null)} onSaved={(agent) => { choose(agent); setRefresh((value) => value + 1); onChanged?.(); }} />
    </div> : selected ? <div className="space-y-8">
      {backButton("All agents", () => choose(null))}
      <div className="space-y-4">
        <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3">
          <div className="flex min-w-0 items-start gap-4"><span className={`${iconTileClass} size-11`} aria-hidden="true"><Bot /></span><div className="min-w-0 space-y-2"><h3 className="break-words text-2xl font-semibold tracking-tight">{selected.name}</h3><div className="flex flex-wrap items-center gap-1.5"><Badge variant="outline">Version {selected.current_version}</Badge>{selected.disabled_at ? <Badge variant="destructive">Disabled</Badge> : null}</div></div></div>
          {!selected.disabled_at ? <Button variant="outline" disabled={busy} onClick={() => setEditing(true)}>Create next version</Button> : null}
        </div>
        <p className="max-w-prose text-sm leading-6 text-muted-foreground">{selected.definition.description}</p>
      </div>
      <Group title="Instructions"><div className={`${cardClass} p-4 sm:p-5`}><p className="whitespace-pre-wrap break-words text-sm leading-7">{selected.definition.system_prompt}</p></div></Group>
      <Group title="Capabilities"><Facts>
        <Fact label="Tools"><span className="flex flex-wrap gap-1.5">{selected.definition.allowed_tools.map((tool) => <span key={tool} className={chipClass}>{toolLabel(tool)}</span>)}</span></Fact>
        <Fact label="Source domains">{selected.definition.permissions.allowed_web_hosts?.length ? <span className="flex flex-wrap gap-1.5">{selected.definition.permissions.allowed_web_hosts.map((host) => <span key={host} className={`${chipClass} font-mono`}>{host}</span>)}</span> : <span className="text-muted-foreground">Inherits the task’s permitted sources</span>}</Fact>
      </Facts></Group>
      <Group title="Test a saved version" note="Runs a real queued task with this agent. Provider configuration applies.">
        <form onSubmit={test} className={`${cardClass} space-y-5 p-4 sm:p-5`}>
          <div className="space-y-2"><label htmlFor="agent-test-version" className="text-sm font-medium">Version</label><select id="agent-test-version" value={testVersion} onChange={(event) => setTestVersion(Number(event.target.value))} className={`${inputClass} h-10`} disabled={busy || !!selected.disabled_at}>{versions.length ? versions.map((version) => <option key={version.id} value={version.version}>Version {version.version} · {formatDate(version.created_at)}</option>) : <option value={selected.current_version}>Version {selected.current_version}</option>}</select></div>
          <div className="space-y-2"><label htmlFor="agent-test-goal" className="text-sm font-medium">Test question</label><textarea id="agent-test-goal" className={`${inputClass} leading-6`} value={testGoal} onChange={(event) => setTestGoal(event.target.value)} required minLength={1} maxLength={8000} rows={4} placeholder="Ask it something it should be able to answer" disabled={busy || !!selected.disabled_at} /></div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2"><Button type="submit" disabled={busy || !!selected.disabled_at || !!testTask}>{busy ? "Starting test…" : "Run test task"}</Button>{testTask ? <p role="status" className="text-sm">Test queued. <button type="button" className="ml-2 rounded text-sm underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/60" onClick={() => setTestTask(null)}>Prepare another test</button></p> : null}</div>
        </form>
      </Group>
      {versions.length ? <Group title="Version history" aside={<span className="text-xs tabular-nums text-muted-foreground">{versions.length} {versions.length === 1 ? "version" : "versions"}</span>}>
        <div className={`${cardClass} divide-y`}>{versions.map((version) => <details key={version.id} className="group/version min-w-0">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-4 py-3 text-sm outline-none transition-colors hover:bg-muted/40 focus-visible:bg-muted/40 motion-reduce:transition-none sm:px-5 [&::-webkit-details-marker]:hidden"><span className="flex min-w-0 items-center gap-2"><ChevronRight className="size-4 shrink-0 text-muted-foreground transition-transform group-open/version:rotate-90 motion-reduce:transition-none" aria-hidden="true" /><span className="font-medium">Version {version.version}</span>{version.version === selected.current_version ? <Badge variant="secondary">Current</Badge> : null}</span><span className="text-xs text-muted-foreground">{formatDate(version.created_at)}</span></summary>
          <div className="space-y-3 border-t bg-muted/20 px-4 py-4 sm:px-5"><p className="break-all font-mono text-[11px] leading-5 text-muted-foreground">SHA-256 {version.sha256}</p><pre className="max-h-80 overflow-auto rounded-lg border bg-background p-4 text-xs leading-6" tabIndex={0}>{JSON.stringify(version.definition, null, 2)}</pre></div>
        </details>)}</div>
      </Group> : null}
      {!selected.disabled_at ? <details className="group/danger rounded-xl border border-destructive/25">
        <summary className="flex cursor-pointer list-none items-center gap-2 rounded-xl px-4 py-3 text-sm text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 motion-reduce:transition-none sm:px-5 [&::-webkit-details-marker]:hidden"><ChevronRight className="size-4 shrink-0 transition-transform group-open/danger:rotate-90 motion-reduce:transition-none" aria-hidden="true" />Disable this agent</summary>
        <div className="space-y-3 border-t border-destructive/25 px-4 py-4 sm:px-5"><p className="max-w-prose text-[0.8125rem] leading-5 text-muted-foreground">Disabling prevents new tasks from selecting this agent. Existing versions and run evidence are preserved.</p><Button variant="destructive" disabled={busy} onClick={() => void disable()}>Disable agent</Button></div>
      </details> : null}
    </div> : <div className="space-y-6">
      <p className="flex items-start gap-3 rounded-xl border bg-muted/30 px-4 py-3 text-[0.8125rem] leading-5 text-muted-foreground"><FlaskConical className="mt-0.5 size-4 shrink-0" aria-hidden="true" />An early feature: chat conversations don’t use custom agents yet. You can still create, version and test them here.</p>
      {loading && !agents.length ? <div role="status" className="grid gap-3 sm:grid-cols-2"><span className="sr-only">Loading agents…</span>{[0, 1].map((index) => <div key={index} className="h-36 animate-pulse rounded-xl border bg-muted/50 motion-reduce:animate-none" aria-hidden="true" />)}</div> : null}
      {agents.length ? <ul className="grid gap-3 sm:grid-cols-2" aria-label="Saved agents">{agents.map((agent) => { const tools = toolsOf(agent).length; return <li key={agent.id}>
        <button type="button" disabled={busy} onClick={() => choose(agent)} className={`group flex h-full w-full flex-col gap-4 rounded-xl border bg-card p-4 text-left text-card-foreground shadow-xs outline-none transition-colors hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring/60 disabled:pointer-events-none disabled:opacity-60 motion-reduce:transition-none ${agent.disabled_at ? "opacity-70" : ""}`}>
          <span className="flex items-start justify-between gap-3"><span className={iconTileClass} aria-hidden="true"><Bot /></span><span className="flex flex-wrap justify-end gap-1.5"><Badge variant="outline">Version {agent.current_version}</Badge>{agent.disabled_at ? <Badge variant="destructive">Disabled</Badge> : null}</span></span>
          <span className="min-w-0 space-y-1"><span className="block break-words text-sm font-medium">{agent.name}</span><span className="line-clamp-2 block text-[0.8125rem] leading-5 text-muted-foreground">{agent.definition.description}</span></span>
          <span className="mt-auto flex items-center justify-between text-xs text-muted-foreground"><span>{tools ? `${tools} ${tools === 1 ? "tool" : "tools"}` : "No tools"}</span><ChevronRight className="size-4 transition-transform group-hover:translate-x-0.5 motion-reduce:transition-none" aria-hidden="true" /></span>
        </button>
      </li>; })}</ul> : null}
      {hasMore ? <div className="flex justify-center"><Button variant="outline" disabled={busy || loadingMore} onClick={() => void loadMore()}>{loadingMore ? "Loading…" : "Load more agents"}</Button></div> : null}
      {!agents.length && !loading ? <EmptyState icon={<Bot />} title="No custom agents yet" action={<Button disabled={busy} onClick={() => choose(null, true)}><Plus />New agent</Button>}>Create one for questions you ask often.</EmptyState> : null}
    </div>}
  </section>;
}
