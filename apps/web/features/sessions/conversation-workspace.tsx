"use client";
import Link from "next/link";
import dynamic from "next/dynamic";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Bot, GitBranch as Github, Menu, PanelLeftClose, Plus, SquarePen, X } from "lucide-react";
import { AccountControl, inputClass } from "@/components/session-boundary";
import { Button } from "@/components/ui/button";
import { SessionHistory } from "@/features/sessions/session-history";
import { ConversationComposer } from "@/features/sessions/conversation-composer";
import { ConversationThread } from "@/features/sessions/conversation-thread";
const AgentManager = dynamic(() => import("@/features/agents/agent-manager").then((module) => module.AgentManager), { loading: () => <p role="status" className="p-8 text-sm text-muted-foreground">Loading agents…</p> });
const GitHubConnection = dynamic(() => import("@/features/github/github-connection").then((module) => module.GitHubConnection), { loading: () => <p role="status" className="p-8 text-sm text-muted-foreground">Loading connection…</p> });
import { api, message, type Project } from "@/lib/task-api";
import type { ConversationSession, TaskMode, TurnInput, TurnResult, WorkspaceConfiguration } from "@/lib/workspace-api";
import type { CustomAgent } from "@/lib/agents-api";

type Surface = "conversation" | "agents" | "github";
export function ConversationWorkspace({ initialSurface = "conversation", createAgent = false }: { initialSurface?: Surface; createAgent?: boolean }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [projectName, setProjectName] = useState("");
  const [sessions, setSessions] = useState<ConversationSession[]>([]);
  const [selected, setSelected] = useState<ConversationSession | null>(null);
  const [config, setConfig] = useState<WorkspaceConfiguration | null>(null);
  const [agents, setAgents] = useState<CustomAgent[]>([]);
  const [mode, setMode] = useState<TaskMode>("research");
  const [previousTaskId, setPreviousTaskId] = useState<string | null>(null);
  const [surface, setSurface] = useState<Surface>(initialSurface);
  const [sidebar, setSidebar] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [threadRefresh, setThreadRefresh] = useState(0);
  const pendingSession = useRef<ConversationSession | null>(null);
  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setSidebar(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const [projectList, configuration] = await Promise.all([api<Project[]>("/projects?limit=100", { signal: controller.signal }), api<WorkspaceConfiguration>("/configuration", { signal: controller.signal })]);
        const requestedId = new URLSearchParams(window.location.search).get("session");
        const requested = requestedId && /^[0-9a-f-]{36}$/i.test(requestedId) ? await api<ConversationSession>(`/sessions/${requestedId}`, { signal: controller.signal }) : null;
        if (!controller.signal.aborted) { setProjects(projectList); setConfig(configuration); setProjectId((current) => requested?.project_id ?? (projectList.some((project) => project.id === current) ? current : projectList[0]?.id ?? "")); if (requested) { setSelected(requested); setMode(requested.mode); } setError(""); setLoading(false); }
      } catch (error) { if (!controller.signal.aborted) { setError(message(error)); setLoading(false); } }
    })();
    return () => controller.abort();
  }, [refresh]);
  useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const [active, archived, definitions] = await Promise.all([
          api<ConversationSession[]>(`/sessions?project_id=${projectId}&archived=false&limit=50`, { signal: controller.signal }),
          api<ConversationSession[]>(`/sessions?project_id=${projectId}&archived=true&limit=50`, { signal: controller.signal }),
          api<CustomAgent[]>(`/agents?project_id=${projectId}`, { signal: controller.signal }),
        ]);
        if (!controller.signal.aborted) { setSessions([...active, ...archived]); setAgents(definitions); setError(""); }
      } catch (error) { if (!controller.signal.aborted) setError(message(error)); }
    })();
    return () => controller.abort();
  }, [projectId, refresh]);
  function urlSession(id: string | null) {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("session", id); else url.searchParams.delete("session");
    url.searchParams.delete("auth_error");
    window.history.replaceState({}, "", url);
  }
  function newChat() { if (busy) return; setPreviousTaskId(null); setSelected(null); pendingSession.current = null; setSurface("conversation"); setSidebar(false); setError(""); urlSession(null); }
  function select(session: ConversationSession) { if (busy) return; setPreviousTaskId(null); setSelected(session); setMode(session.mode); setSurface("conversation"); setSidebar(false); pendingSession.current = null; setError(""); urlSession(session.id); }
  const updateSession = useCallback((session: ConversationSession) => {
    setSessions((current) => [session, ...current.filter((item) => item.id !== session.id)].sort((a, b) => b.updated_at.localeCompare(a.updated_at)));
    setSelected((current) => current?.id === session.id ? session : current);
  }, []);
  async function createProject(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { const project = await api<Project>("/projects", { method: "POST", body: JSON.stringify({ name: projectName.trim() }) }); setProjects((current) => [project, ...current]); setProjectId(project.id); setProjectName(""); setSessions([]); newChat(); }
    catch (error) { setError(message(error)); } finally { setBusy(false); }
  }
  async function send(input: TurnInput) {
    setBusy(true);
    try {
    let current = selected ?? pendingSession.current;
    let project = projectId;
    if (!project) {
      const created = await api<Project>("/projects", { method: "POST", body: JSON.stringify({ name: "Personal workspace" }) });
      setProjects((current) => [created, ...current]); setProjectId(created.id); project = created.id;
    }
    if (!current) {
      current = await api<ConversationSession>("/sessions", { method: "POST", body: JSON.stringify({ project_id: project, title: input.goal.slice(0, 80), mode: input.kind }) });
      pendingSession.current = current;
    }
    const result = await api<TurnResult>(`/sessions/${current.id}/turns`, { method: "POST", body: JSON.stringify(input) });
    const updated = { ...current, mode: input.kind, active_task_id: result.task.id, updated_at: result.task.created_at };
    pendingSession.current = null; setSelected(updated); updateSession(updated); setThreadRefresh((value) => value + 1); urlSession(updated.id);
    } finally { setBusy(false); }
  }
  const title = surface === "agents" ? "Custom agents" : surface === "github" ? "Connections" : selected?.title ?? "New conversation";
  return <div className="flex h-svh min-h-0 overflow-hidden bg-background">
    {sidebar ? <button className="fixed inset-0 z-30 bg-foreground/20 lg:hidden" aria-label="Close navigation" onClick={() => setSidebar(false)} /> : null}
    <aside className={`fixed inset-y-0 left-0 z-40 flex w-64 shrink-0 flex-col border-r bg-sidebar px-3 pb-4 pt-5 transition-transform motion-reduce:transition-none lg:static lg:translate-x-0 ${sidebar ? "translate-x-0" : "-translate-x-full max-lg:invisible"} ${collapsed ? "lg:hidden" : ""}`} aria-label="Workspace navigation">
      <div className="mb-6 flex items-center justify-between px-2"><Link href="/" className="inline-flex items-center gap-2 text-sm font-semibold focus-visible:outline-2"><span className="flex size-6 items-center justify-center rounded-md bg-primary text-xs text-primary-foreground" aria-hidden="true">A</span>AgentForge</Link><Button variant="ghost" size="icon-sm" aria-label="Collapse sidebar" onClick={() => { setSidebar(false); setCollapsed(true); }}><PanelLeftClose /></Button></div>
      <Button variant="secondary" className="mb-3 min-h-10 justify-start gap-3 border bg-background" onClick={newChat} disabled={busy}><SquarePen />New conversation</Button>
      <nav className="mb-6 space-y-1" aria-label="Workspace tools"><button className={`flex min-h-10 w-full items-center gap-3 rounded-lg px-3 text-[13px] hover:bg-sidebar-accent focus-visible:outline-2 ${surface === "agents" ? "bg-sidebar-accent" : ""}`} onClick={() => { setSurface("agents"); setSidebar(false); }}><Bot className="size-4 text-muted-foreground" aria-hidden="true" />Custom agents</button><button className={`flex min-h-10 w-full items-center gap-3 rounded-lg px-3 text-[13px] hover:bg-sidebar-accent focus-visible:outline-2 ${surface === "github" ? "bg-sidebar-accent" : ""}`} onClick={() => { setSurface("github"); setSidebar(false); }}><Github className="size-4 text-muted-foreground" aria-hidden="true" />GitHub connection</button></nav>
      {projects.length ? <div className="mb-3 px-1"><label htmlFor="workspace-project" className="sr-only">Current project</label><select id="workspace-project" className={`${inputClass} border-transparent bg-muted/40 text-xs`} value={projectId} onChange={(event) => { setProjectId(event.target.value); setSessions([]); newChat(); }} disabled={busy}>{projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></div> : null}
      <SessionHistory projectId={projectId} sessions={sessions} selectedId={selected?.id ?? null} loading={loading} onSelect={select} onChange={updateSession} />
      <details className="mb-4 border-t px-2 pt-4"><summary className="flex cursor-pointer list-none items-center gap-2 text-xs text-muted-foreground focus-visible:outline-2"><Plus className="size-3.5" aria-hidden="true" />New project</summary><form onSubmit={createProject} className="mt-3 space-y-2"><label htmlFor="new-project" className="sr-only">Project name</label><input id="new-project" className={inputClass} placeholder="Project name" value={projectName} onChange={(event) => setProjectName(event.target.value)} maxLength={100} required disabled={busy} /><Button variant="outline" type="submit" size="sm" disabled={busy || !projectName.trim()}>Create project</Button></form></details>
      <div className="space-y-4 border-t px-2 pt-4"><Link href="/settings" className="inline-flex min-h-8 items-center text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2">Workspace settings</Link><AccountControl /></div>
    </aside>
    <div className="flex min-w-0 flex-1 flex-col">
      <header className="flex min-h-16 shrink-0 items-center gap-3 px-5 sm:px-8"><Button variant="ghost" size="icon" aria-label="Open navigation" className={collapsed ? "" : "lg:hidden"} onClick={() => { setCollapsed(false); setSidebar(true); }}><Menu /></Button><p className="min-w-0 flex-1 truncate text-sm text-muted-foreground">{title}</p>{selected?.archived && surface === "conversation" ? <span className="text-xs text-muted-foreground">Archived</span> : null}{surface !== "conversation" ? <Button variant="ghost" size="sm" onClick={() => setSurface("conversation")}><X />Close</Button> : null}</header>
      {error ? <div role="alert" className="mx-5 mb-3 flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3 text-sm text-destructive"><p>{error}</p><Button variant="outline" size="sm" onClick={() => setRefresh((value) => value + 1)}>Retry</Button></div> : null}
      <main data-conversation-scroll className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto overscroll-contain">
        {surface === "agents" ? <AgentManager key={projectId} projectId={projectId} initialCreate={createAgent} onChanged={() => setRefresh((value) => value + 1)} /> : surface === "github" ? <GitHubConnection /> : <>
          {selected ? <ConversationThread key={selected.id} session={selected} refresh={threadRefresh} onChange={updateSession} onLatestTaskId={setPreviousTaskId} /> : <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col justify-center px-5 pb-6 pt-12 sm:px-8 sm:pt-20"><h1 className="mb-3 text-center text-3xl font-semibold tracking-tight sm:text-4xl">What are we working on?</h1><p className="mx-auto mb-9 max-w-md text-center text-sm leading-6 text-muted-foreground">A question to research, a problem to solve, a change to build. Give your agents a clear outcome.</p><ConversationComposer key="new" mode={mode} onModeChange={setMode} config={config} disabled={loading || busy} archived={false} agentOptions={agents.filter((agent) => !agent.disabled_at).map((agent) => ({ key: agent.key, name: agent.name, version: agent.current_version }))} onSend={send} /><p className="mt-4 text-center text-[11px] text-muted-foreground">{loading ? "Connecting to your workspace…" : projects.length ? `Working in ${projects.find((project) => project.id === projectId)?.name ?? "your project"}` : "Your first conversation creates a personal workspace."}</p></div>}
          {selected ? <div className="sticky bottom-0 mt-auto bg-background px-5 pb-5 pt-5 sm:px-8"><div className="mx-auto max-w-[calc(48rem-4rem)]"><ConversationComposer key={selected.id} previousTaskId={previousTaskId} mode={mode} onModeChange={setMode} config={config} disabled={busy || !!selected.active_task_id} archived={selected.archived} agentOptions={agents.filter((agent) => !agent.disabled_at).map((agent) => ({ key: agent.key, name: agent.name, version: agent.current_version }))} onSend={send} /></div></div> : null}
        </>}
      </main>
    </div>
  </div>;
}
