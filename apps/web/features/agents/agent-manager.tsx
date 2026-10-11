"use client";
import { useEffect, useState, type FormEvent } from "react";
import { Bot, Plus, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { AgentEditor } from "@/features/agents/agent-editor";
import { inputClass } from "@/components/session-boundary";
import { api, formatDate, message, type Task } from "@/lib/task-api";
import type { AgentVersion, CustomAgent } from "@/lib/agents-api";

const toolLabels: Record<string, string> = { read_task_input: "Read task inputs", read_research_source: "Read saved sources", web_search: "Search the web", fetch_webpage: "Fetch source pages" };

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
  return <section className={embedded ? "space-y-6" : "mx-auto w-full max-w-5xl space-y-8 px-5 py-9 sm:px-8"} aria-labelledby="agents-title">
    <header className="flex flex-wrap items-start justify-between gap-4"><div className="space-y-2"><h2 id="agents-title" className="text-lg font-semibold">Custom agents</h2><p className="max-w-prose text-sm leading-6 text-muted-foreground">Create specialized assistants and manage their instructions.</p></div><div className="flex gap-2"><Button variant="ghost" size="icon" aria-label="Refresh custom agents" disabled={busy || loadingMore || loading} onClick={() => { setLoading(true); setRefresh((value) => value + 1); }}><RefreshCw /></Button><Button variant="outline" disabled={busy} onClick={() => choose(null, true)}><Plus />New agent</Button></div></header>
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    <div className="grid gap-6 xl:grid-cols-[11rem_minmax(0,1fr)]">
      <div className="space-y-3"><h2 className="text-xs font-medium text-muted-foreground">Saved agents</h2>{loading ? <p role="status" className="text-sm text-muted-foreground">Loading agents…</p> : null}<ul className="space-y-1">{agents.map((agent) => <li key={agent.id}><button disabled={busy} aria-pressed={selected?.id === agent.id} onClick={() => choose(agent)} className={`w-full space-y-1 rounded-lg p-3 text-left focus-visible:outline-2 ${selected?.id === agent.id ? "bg-muted" : "hover:bg-muted/60"}`}><span className="block break-words text-sm font-medium">{agent.name}</span><span className="block text-xs text-muted-foreground">Version {agent.current_version}{agent.disabled_at ? " · Disabled" : ""}</span></button></li>)}</ul>{hasMore ? <Button variant="ghost" size="sm" disabled={busy || loadingMore} onClick={() => void loadMore()}>{loadingMore ? "Loading…" : "Load more agents"}</Button> : null}{!agents.length && !loading ? <p className="text-sm leading-6 text-muted-foreground">No custom agents yet. Create one for questions you ask often.</p> : null}</div>
      <div className="min-w-0 space-y-8">{editing ? <AgentEditor key={`${selected?.id ?? "new"}-${selected?.current_version ?? 0}`} projectId={selected?.project_id ?? projectId} existing={selected} onSaved={(agent) => { choose(agent); setRefresh((value) => value + 1); onChanged?.(); }} /> : selected ? <>
        <div className="space-y-4"><div className="flex flex-wrap items-start justify-between gap-4"><div className="space-y-2"><h2 className="text-xl font-semibold">{selected.name}</h2><Badge variant="outline">Version {selected.current_version}{selected.disabled_at ? " · Disabled" : ""}</Badge></div>{!selected.disabled_at ? <Button variant="outline" disabled={busy} onClick={() => setEditing(true)}>Create next version</Button> : null}</div><p className="text-sm leading-6 text-muted-foreground">{selected.definition.description}</p><div><h3 className="mb-2 text-xs font-semibold">Instructions</h3><p className="whitespace-pre-wrap break-words text-sm leading-7">{selected.definition.system_prompt}</p></div><p className="text-xs text-muted-foreground">Tools: {selected.definition.allowed_tools.map((tool) => toolLabels[tool] ?? tool.replaceAll("_", " ")).join(", ")}</p></div>
        <form onSubmit={test} className="space-y-4 border-t pt-6"><h3 className="text-sm font-semibold">Test a saved version</h3><div className="space-y-2"><label htmlFor="agent-test-version" className="text-xs font-medium">Version</label><select id="agent-test-version" value={testVersion} onChange={(event) => setTestVersion(Number(event.target.value))} className={inputClass} disabled={busy || !!selected.disabled_at}>{versions.length ? versions.map((version) => <option key={version.id} value={version.version}>Version {version.version} · {formatDate(version.created_at)}</option>) : <option value={selected.current_version}>Version {selected.current_version}</option>}</select></div><div className="space-y-2"><label htmlFor="agent-test-goal" className="text-xs font-medium">Test question</label><textarea id="agent-test-goal" className={inputClass} value={testGoal} onChange={(event) => setTestGoal(event.target.value)} required minLength={1} maxLength={8000} rows={4} disabled={busy || !!selected.disabled_at} /></div><p className="text-xs leading-5 text-muted-foreground">Runs a real queued task with this agent. Provider configuration applies.</p><Button variant="outline" type="submit" disabled={busy || !!selected.disabled_at || !!testTask}>{busy ? "Starting test…" : "Run test task"}</Button>{testTask ? <p role="status" className="text-sm">Test queued. <button type="button" className="ml-2 text-xs underline underline-offset-4" onClick={() => setTestTask(null)}>Prepare another test</button></p> : null}</form>
        <div className="space-y-3 border-t pt-6"><h3 className="text-sm font-semibold">Version history</h3>{versions.map((version) => <details key={version.id} className="min-w-0 border-b py-3"><summary className="cursor-pointer text-xs focus-visible:outline-2">Version {version.version} · {formatDate(version.created_at)}</summary><p className="my-3 break-all font-mono text-[11px] text-muted-foreground">SHA-256 {version.sha256}</p><pre className="max-h-80 overflow-auto rounded-lg bg-muted/50 p-4 text-xs leading-6" tabIndex={0}>{JSON.stringify(version.definition, null, 2)}</pre></details>)}</div>
        {!selected.disabled_at ? <details className="border-t pt-5"><summary className="cursor-pointer text-xs text-muted-foreground focus-visible:outline-2">Disable this agent</summary><p className="my-3 text-xs leading-5 text-muted-foreground">Disabling prevents new tasks from selecting this agent. Existing versions and run evidence are preserved.</p><Button variant="destructive" disabled={busy} onClick={() => void disable()}>Disable agent</Button></details> : null}
      </> : <div className="flex min-h-56 flex-col items-center justify-center gap-3 text-center"><Bot className="size-6 text-muted-foreground" aria-hidden="true" /><h2 className="text-lg font-medium">Choose an agent</h2><p className="max-w-sm text-sm leading-6 text-muted-foreground">Inspect instructions, save a new version, or run a test.</p></div>}</div>
    </div>
  </section>;
}
