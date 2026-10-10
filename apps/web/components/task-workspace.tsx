"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { ArrowRight, Plus, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { inputClass } from "@/components/session-boundary";
import { api, formatDate, message, statusLabel, type Project, type Task } from "@/lib/task-api";

type Configuration = {
  research_configured: boolean;
  coding_configured: boolean;
  coding_missing_settings: string[];
  missing_settings: string[];
  tracing_configured: boolean;
  qualification: string;
};

export function TaskWorkspace() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [config, setConfig] = useState<Configuration | null>(null);
  const [projectId, setProjectId] = useState("");
  const [kind, setKind] = useState<"research" | "coding">("research");
  const [repository, setRepository] = useState("");
  const [repositoryRef, setRepositoryRef] = useState("main");
  const [issueNumber, setIssueNumber] = useState("");
  const [testProfile, setTestProfile] = useState("python_pytest");
  const [alsoBuild, setAlsoBuild] = useState(false);
  const [goal, setGoal] = useState("");
  const [cost, setCost] = useState("0.50");
  const [seconds, setSeconds] = useState("300");
  const [hosts, setHosts] = useState("");
  const [projectName, setProjectName] = useState("");
  const [error, setError] = useState("");
  const [submitError, setSubmitError] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [offset, setOffset] = useState(0);
  const [createdTask, setCreatedTask] = useState<Task | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    async function load() {
      if (inFlight) return;
      inFlight = true;
      try {
        const [nextProjects, nextTasks, nextConfig] = await Promise.all([
          api<Project[]>("/projects", { signal: controller.signal }),
          api<Task[]>(`/tasks?limit=20&offset=${offset}`, { signal: controller.signal }),
          api<Configuration>("/configuration", { signal: controller.signal }),
        ]);
        if (!controller.signal.aborted) {
          setProjects(nextProjects);
          setProjectId((current) => nextProjects.some((project) => project.id === current) ? current : nextProjects[0]?.id ?? "");
          setTasks(nextTasks);
          setConfig(nextConfig);
          setError("");
        }
      } catch (error) {
        if (!controller.signal.aborted) setError(message(error));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
        inFlight = false;
      }
    }
    void load();
    const timer = window.setInterval(() => { void load(); }, 10000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [refresh, offset]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitError("");
    const numericCost = Number(cost);
    const numericSeconds = Number(seconds);
    const allowedHosts = (kind === "research" ? hosts : "").split(/[\s,]+/).map((host) => host.trim().toLowerCase()).filter(Boolean);
    if (!projectId || goal.trim().length < 1 || goal.trim().length > 8000 || !Number.isFinite(numericCost) || numericCost <= 0 || numericCost > 5 || !Number.isInteger(numericSeconds) || numericSeconds < 30 || numericSeconds > 1200 || allowedHosts.length > 20) {
      setSubmitError("Choose a project, write a clear, nonempty message, and keep the limits within the ranges shown.");
      return;
    }
    if (kind === "coding" && (!/^[A-Za-z0-9-]+\/[A-Za-z0-9_.-]+$/.test(repository.trim()) || !repositoryRef.trim() || (issueNumber && (!/^\d+$/.test(issueNumber) || Number(issueNumber) < 1 || Number(issueNumber) > 2147483647)))) {
      setSubmitError("Enter owner/name, a branch or commit, and an optional positive issue number.");
      return;
    }
    setSubmitting(true);
    try {
      const task = await api<Task>("/tasks", { method: "POST", body: JSON.stringify({
        project_id: projectId, kind, goal: goal.trim(), max_cost_usd: cost,
        ...(kind === "coding" ? { coding: { repository: { repo_id: repository.trim(), ref: repositoryRef.trim() }, issue_number: issueNumber ? Number(issueNumber) : null, test_profiles: [testProfile], command_profiles: alsoBuild ? ["node_build"] : [], max_repair_attempts: 2 } } : {}),
        max_wall_time_sec: numericSeconds, allowed_web_hosts: allowedHosts,
      }) });
      // Show a durable receipt before navigation; a failed navigation must never
      // encourage an accidental second paid task submission.
      setCreatedTask(task);
      setGoal("");
      setOffset(0);
      setRefresh((value) => value + 1);
    } catch (error) { setSubmitError(message(error)); }
    finally { setSubmitting(false); }
  }

  return (
    <div className="grid items-start gap-12 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)] lg:gap-16">
      <section aria-labelledby="composer-title" className="min-w-0 space-y-7">
        <div className="space-y-3">
          <h1 id="composer-title" className="text-3xl font-semibold tracking-tight">What would you like your agents to do?</h1>
          <p className="max-w-prose text-sm leading-relaxed text-muted-foreground">Research a question or prepare a code change. Set a budget, follow the work, and review the evidence before using the result.</p>
        </div>
        {loading ? <div aria-label="Loading workspace" className="space-y-4"><div className="h-10 rounded-lg bg-muted" /><div className="h-44 rounded-lg bg-muted" /></div> : null}
        {error ? <div role="alert" className="space-y-3"><p className="text-sm text-destructive">{error}</p><Button variant="outline" onClick={() => setRefresh((value) => value + 1)}>Retry</Button></div> : null}
        {config && !(kind === "coding" ? config.coding_configured : config.research_configured) ? (
          <div className="rounded-xl border bg-muted/30 p-4 text-sm leading-relaxed">
            <p className="font-medium">{kind === "coding" ? "Coding setup is incomplete" : "Research setup is incomplete"}</p>
            <p className="mt-1 text-muted-foreground">Configure the following API settings, restart the API and worker, then refresh this page:</p>
            <ul className="mt-3 list-inside list-disc break-words text-xs leading-6">{(kind === "coding" ? config.coding_missing_settings : config.missing_settings).map((name) => <li key={name}><code>{name}</code></li>)}</ul>
          </div>
        ) : null}
        {config && !config.tracing_configured ? <p className="text-xs leading-relaxed text-muted-foreground">Neatlogs tracing is not configured. Tasks can run, but external trace delivery requires NEATLOGS_API_KEY.</p> : null}
        {!loading && !projects.length && !error ? (
          <form className="space-y-3 rounded-xl border p-4" onSubmit={async (event) => {
            event.preventDefault(); setCreatingProject(true); setSubmitError("");
            try {
              const project = await api<Project>("/projects", { method: "POST", body: JSON.stringify({ name: projectName.trim() }) });
              setProjects([project]); setProjectId(project.id); setProjectName("");
            } catch (error) { setSubmitError(message(error)); }
            finally { setCreatingProject(false); }
          }}>
            <p className="text-sm font-medium">Create a project to organize your tasks</p>
            <label htmlFor="project-name" className="sr-only">Project name</label>
            <input id="project-name" className={inputClass} placeholder="Project name" required maxLength={100} value={projectName} onChange={(event) => setProjectName(event.target.value)} disabled={creatingProject} />
            <Button type="submit" disabled={creatingProject}><Plus aria-hidden="true" />{creatingProject ? "Creating…" : "Create project"}</Button>
          </form>
        ) : null}
        <form onSubmit={submit} className="space-y-5" aria-busy={submitting}>
          <div className="space-y-2">
            <label htmlFor="task-kind" className="text-sm font-medium">Task type</label>
            <select id="task-kind" className={inputClass} value={kind} onChange={(event) => setKind(event.target.value as "research" | "coding")} disabled={submitting}>
              <option value="research">Research</option><option value="coding">Code change</option>
            </select>
          </div>
          <div className="space-y-2">
            <label htmlFor="project" className="text-sm font-medium">Project</label>
            <select id="project" required className={inputClass} value={projectId} onChange={(event) => setProjectId(event.target.value)} disabled={loading || submitting || !projects.length}>
              {!projects.length ? <option value="">No projects available</option> : null}
              {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
            </select>
          </div>
          {kind === "coding" ? <fieldset className="space-y-4 border-t pt-5" disabled={submitting}>
            <legend className="px-1 text-sm font-medium">Public GitHub repository</legend>
            <div className="space-y-2"><label htmlFor="repository" className="text-sm">Repository owner/name</label><input id="repository" className={inputClass} required maxLength={140} value={repository} onChange={(event) => setRepository(event.target.value)} placeholder="owner/repository" autoCapitalize="none" spellCheck={false} /></div>
            <div className="space-y-2"><label htmlFor="repository-ref" className="text-sm">Branch, tag, or full commit SHA</label><input id="repository-ref" className={inputClass} required maxLength={200} value={repositoryRef} onChange={(event) => setRepositoryRef(event.target.value)} autoCapitalize="none" spellCheck={false} /><p className="text-xs leading-relaxed text-muted-foreground">The server pins this ref to an immutable commit before queuing. Private repositories are unavailable.</p></div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2"><label htmlFor="issue-number" className="text-sm">Issue number (optional)</label><input id="issue-number" className={inputClass} type="number" min="1" max="2147483647" step="1" value={issueNumber} onChange={(event) => setIssueNumber(event.target.value)} /></div>
              <div className="space-y-2"><label htmlFor="test-profile" className="text-sm">Test profile</label><select id="test-profile" className={inputClass} value={testProfile} onChange={(event) => setTestProfile(event.target.value)}><option value="python_pytest">Python · pytest</option><option value="python_unittest">Python · unittest</option><option value="node_npm_test">Node · npm test</option></select></div>
            </div>
            <div className="space-y-2">
              <label htmlFor="also-build" className="flex items-center gap-3 text-sm"><input id="also-build" type="checkbox" className="size-4 accent-primary focus-visible:outline-2 focus-visible:outline-offset-2" checked={alsoBuild} onChange={(event) => setAlsoBuild(event.target.checked)} aria-describedby="also-build-help" />Also run npm build</label>
              <p id="also-build-help" className="text-xs leading-relaxed text-muted-foreground">Optional. Uses the same task budget. The E2B template must already have the repository&apos;s build dependencies installed.</p>
            </div>
            <p className="text-xs leading-relaxed text-muted-foreground">Code runs in an E2B managed sandbox. Review the patch and actual test logs; these tasks do not publish changes to GitHub.</p>
          </fieldset> : null}
          <div className="space-y-2">
            <label htmlFor="goal" className="text-sm font-medium">{kind === "coding" ? "Change request" : "Research question"}</label>
            <textarea id="goal" className={`${inputClass} min-h-44 resize-y leading-relaxed`} placeholder={kind === "coding" ? "Describe the change, expected behavior, and acceptance criteria." : "Describe the question, the evidence you need, and what a useful answer should include."} minLength={1} maxLength={8000} required value={goal} onChange={(event) => setGoal(event.target.value)} disabled={submitting || loading} aria-describedby="goal-help" />
            <p id="goal-help" className="text-xs text-muted-foreground">1–8,000 characters. Be specific about the desired outcome.</p>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2"><label htmlFor="cost" className="text-sm font-medium">Maximum cost (USD)</label><input id="cost" className={`${inputClass} tabular-nums`} type="number" min="0.0001" max="5" step="0.0001" required value={cost} onChange={(event) => setCost(event.target.value)} disabled={submitting} aria-describedby="cost-help" /><p id="cost-help" className="text-xs text-muted-foreground">Up to $5 per task</p></div>
            <div className="space-y-2"><label htmlFor="seconds" className="text-sm font-medium">Time limit (seconds)</label><input id="seconds" className={`${inputClass} tabular-nums`} type="number" min="30" max="1200" step="1" required value={seconds} onChange={(event) => setSeconds(event.target.value)} disabled={submitting} aria-describedby="time-help" /><p id="time-help" className="text-xs text-muted-foreground">30–1,200 seconds</p></div>
          </div>
          {kind === "research" ? <details className="text-sm">
            <summary className="cursor-pointer font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring">Restrict source domains</summary>
            <div className="mt-3 space-y-2"><label htmlFor="hosts" className="text-sm">Allowed public domains</label><input id="hosts" className={inputClass} value={hosts} onChange={(event) => setHosts(event.target.value)} disabled={submitting} placeholder="docs.example.com, example.org" aria-describedby="hosts-help" /><p id="hosts-help" className="text-xs leading-relaxed text-muted-foreground">Optional. Agents can retrieve public sources discovered by this task&apos;s search. Add up to 20 exact domains to restrict retrieval further. Separate domains with commas; subdomains need their own entry.</p></div>
          </details> : null}
          {submitError ? <p role="alert" className="text-sm text-destructive">{submitError}</p> : null}
          {createdTask ? <div role="status" className="rounded-xl border bg-muted/30 p-4 text-sm"><p>{createdTask.kind === "coding" ? "Coding task" : "Research task"} created.</p><Link href={`/tasks/${createdTask.id}`} className="mt-2 inline-flex items-center gap-2 font-medium underline underline-offset-4">Open task<ArrowRight aria-hidden="true" className="size-4" /></Link></div> : null}
          <Button type="submit" size="lg" disabled={submitting || loading || !projectId || !(kind === "coding" ? config?.coding_configured : config?.research_configured)}>{submitting ? "Creating task…" : kind === "coding" ? "Start code change" : "Start research"}<ArrowRight aria-hidden="true" /></Button>
          <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">Task creation queues work for the separate worker. Configuration presence does not confirm that a worker or provider is available.</p>
        </form>
      </section>

      <section aria-labelledby="recent-title" className="min-w-0 border-t pt-7 lg:border-t-0 lg:pt-0">
        <div className="mb-5 flex items-center justify-between gap-3"><h2 id="recent-title" className="text-lg font-semibold">Recent tasks</h2><Button variant="ghost" size="sm" onClick={() => setRefresh((value) => value + 1)} disabled={loading}><RefreshCw aria-hidden="true" />Refresh</Button></div>
        {loading ? <div aria-label="Loading tasks" className="space-y-3">{[0, 1, 2].map((item) => <div key={item} className="h-20 rounded-lg bg-muted" />)}</div> : !tasks.length ? <div className="border-y py-10"><p className="text-sm font-medium">{offset ? "No more tasks" : "Your work starts here"}</p><p className="mt-2 text-sm leading-relaxed text-muted-foreground">{offset ? "Return to the previous page to see earlier results." : "Create a task to see its plan, agent runs, and evidence in this workspace."}</p></div> : <ul className="divide-y border-y">{tasks.map((task) => <li key={task.id}><Link href={`/tasks/${task.id}`} className="block space-y-2 py-5 outline-none hover:bg-muted/30 focus-visible:ring-2 focus-visible:ring-ring"><div className="flex flex-wrap items-center justify-between gap-2"><Badge variant={task.status === "FAILED" ? "destructive" : "secondary"}>{statusLabel(task.status)}</Badge><time dateTime={task.created_at} className="text-xs text-muted-foreground">{formatDate(task.created_at)}</time></div><p className="line-clamp-2 text-sm font-medium leading-relaxed">{task.goal}</p><p className="text-xs text-muted-foreground">{projects.find((project) => project.id === task.project_id)?.name ?? "Project"} · ${task.max_cost_usd} limit</p></Link></li>)}</ul>}
        {offset || tasks.length === 20 ? <div className="mt-4 flex justify-between gap-3"><Button variant="outline" size="sm" disabled={!offset} onClick={() => { setLoading(true); setOffset((value) => Math.max(0, value - 20)); }}>Previous</Button><Button variant="outline" size="sm" disabled={tasks.length < 1 || offset >= 9980} onClick={() => { setLoading(true); setOffset((value) => value + 20); }}>Next</Button></div> : null}
      </section>
    </div>
  );
}
