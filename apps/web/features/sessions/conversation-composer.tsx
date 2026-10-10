"use client";
import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowUp, FileText, Paperclip, Settings2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/session-boundary";
import { api, message, terminalStatuses, type Task, type CodingDetail } from "@/lib/task-api";
import type { GitHubIssue, GitHubRepository } from "@/lib/github-api";
import type { Attachment, TaskMode, TurnInput, WorkspaceConfiguration } from "@/lib/workspace-api";

export function ConversationComposer({ mode, onModeChange, config, disabled, archived, previousTaskId, agentOptions = [], onSend }: { previousTaskId?: string | null; mode: TaskMode; onModeChange: (mode: TaskMode) => void; config: WorkspaceConfiguration | null; disabled: boolean; archived: boolean; agentOptions?: Array<{ key: string; name: string; version: number }>; onSend: (input: TurnInput) => Promise<void> }) {
  const [goal, setGoal] = useState("");
  const [cost, setCost] = useState("0.50");
  const [seconds, setSeconds] = useState("300");
  const [hosts, setHosts] = useState("");
  const [repo, setRepo] = useState("");
  const [ref, setRef] = useState("main");
  const [issue, setIssue] = useState("");
  const [test, setTest] = useState("python_pytest");
  const [build, setBuild] = useState(false);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [agent, setAgent] = useState("");
  const [previousCoding, setPreviousCoding] = useState<Task | null>(null);
  const [continuePrevious, setContinuePrevious] = useState(true);
  const [repositories, setRepositories] = useState<GitHubRepository[]>([]);
  const [issues, setIssues] = useState<GitHubIssue[]>([]);
  const [error, setError] = useState("");
  const [repoError, setRepoError] = useState("");
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const submission = useRef<{ fingerprint: string; key: string } | null>(null);
  const configured = mode === "coding" ? config?.coding_configured : mode === "general" ? config?.general_configured : config?.research_configured;
  const missing = mode === "coding" ? config?.coding_missing_settings : mode === "general" ? config?.general_missing_settings : config?.missing_settings;
  const locked = disabled || archived || busy;
  const costLimit = Number(config?.per_task_cost_limit_usd ?? 1);
  const effectiveCost = Number(cost) > costLimit ? String(costLimit) : cost;
  useEffect(() => {
    if (mode !== "coding" || !previousTaskId) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const task = await api<Task>(`/tasks/${previousTaskId}`, { signal: controller.signal });
        if (task.kind !== "coding" || !task.coding || !terminalStatuses.has(task.status)) return;
        const detail = await api<CodingDetail>(`/tasks/${previousTaskId}/coding`, { signal: controller.signal });
        if (!controller.signal.aborted && detail.session) { setPreviousCoding(task); setContinuePrevious(true); }
      } catch { /* A fresh selected-commit task remains available. */ }
    })();
    return () => controller.abort();
  }, [mode, previousTaskId]);
  const continuation = continuePrevious && previousCoding?.id === previousTaskId ? previousCoding : null;
  useEffect(() => {
    if (mode !== "coding") return;
    const controller = new AbortController();
    void api<{ repositories: GitHubRepository[] }>("/github/repositories", { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) { setRepositories(value.repositories); setRepoError(""); }
    }).catch(() => { if (!controller.signal.aborted) setRepoError(config?.coding_repository_access === "public_only" ? "Connected repositories are unavailable. You can enter a public repository below." : "Connected repositories are unavailable. Check your GitHub installation access and retry."); });
    return () => controller.abort();
  }, [mode, config?.coding_repository_access]);
  useEffect(() => {
    if (mode !== "coding" || !repositories.some((item) => item.full_name === repo)) return;
    const controller = new AbortController();
    void api<{ issues: GitHubIssue[] }>(`/github/issues?repo_id=${encodeURIComponent(repo)}`, { signal: controller.signal }).then((value) => {
      if (!controller.signal.aborted) setIssues(value.issues);
    }).catch(() => { if (!controller.signal.aborted) setIssues([]); });
    return () => controller.abort();
  }, [mode, repo, repositories]);
  async function attach(files: FileList | null) {
    if (!files) return;
    setError("");
    try {
      if (files.length + attachments.length > 4) throw new Error("Attach up to four text files per turn.");
      const additions: Attachment[] = [];
      for (const file of Array.from(files)) {
        if (file.size > 16000 || file.name.length > 120 || !/\.(txt|md|csv|json|py|js|jsx|ts|tsx|html|css|yaml|yml)$/i.test(file.name)) throw new Error("Choose UTF-8 text or source files up to 8,000 characters each.");
        const bytes = await file.arrayBuffer();
        const content = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
        if (!content.length) throw new Error("Empty files cannot be attached. Choose a file containing text.");
        if (content.length > 8000) throw new Error("Keep each attachment below 8,000 characters.");
        if (content.includes("\0")) throw new Error("Binary files cannot be attached. Choose a text file.");
        additions.push({ name: file.name, content });
      }
      if ([...attachments, ...additions].reduce((sum, item) => sum + new TextEncoder().encode(item.content).length, 0) > 16000) throw new Error("Keep the combined attachments below 16 KB.");
      setAttachments((current) => [...current, ...additions]);
    } catch (error) { setError(message(error)); }
    if (fileInput.current) fileInput.current.value = "";
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); setError("");
    if (locked || !configured) return;
    const allowedHosts = hosts.split(/[\s,]+/).map((host) => host.trim().toLowerCase()).filter(Boolean);
    const choice = agentOptions.find((option) => option.key === agent);
    const input: Omit<TurnInput, "idempotency_key"> = { goal: goal.trim(), kind: mode, max_cost_usd: effectiveCost, max_wall_time_sec: Number(seconds), allowed_web_hosts: mode === "coding" ? [] : allowedHosts, attachments: mode === "coding" ? [] : attachments, ...(choice && mode === "research" ? { agent_key: choice.key, agent_version: choice.version } : {}), ...(mode === "coding" && continuation ? { continue_from_task_id: continuation.id } : {}), ...(mode === "coding" ? { coding: { repository: continuation?.coding?.repository ?? { repo_id: repo.trim(), ref: ref.trim() }, issue_number: issue ? Number(issue) : null, test_profiles: [test], command_profiles: build ? ["node_build"] : [], max_repair_attempts: 2 } } : {}) };
    if (input.goal.length < 1 || allowedHosts.length > 20) { setError("Describe the outcome in a clear, nonempty message, with up to 20 source domains."); return; }
    if (mode === "coding" && !continuation && (!/^[A-Za-z0-9-]+\/[A-Za-z0-9_.-]+$/.test(repo.trim()) || !ref.trim())) { setError("Enter a repository as owner/name and a branch or commit."); return; }
    const fingerprint = JSON.stringify(input);
    if (submission.current?.fingerprint !== fingerprint) submission.current = { fingerprint, key: crypto.randomUUID() };
    setBusy(true);
    try { await onSend({ ...input, idempotency_key: submission.current.key }); setGoal(""); setAttachments([]); submission.current = null; }
    catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }
  return <form onSubmit={submit} className="w-full space-y-3" aria-busy={busy}>
    {mode === "coding" && previousCoding && previousCoding.id === previousTaskId ? <label className="flex items-start gap-2 rounded-lg bg-muted/40 px-4 py-3 text-xs leading-5"><input type="checkbox" checked={continuePrevious} onChange={(event) => setContinuePrevious(event.target.checked)} disabled={locked} className="mt-0.5 size-4 accent-primary" /><span>Continue the previous coding workspace in {previousCoding.coding?.repository.repo_id}. New tests run against the carried-forward files and original baseline.</span></label> : null}
    {mode === "coding" ? <fieldset disabled={locked} className="grid gap-3 rounded-xl border bg-muted/20 p-4 sm:grid-cols-2"><legend className="sr-only">Repository and verification</legend>
      {repositories.length ? <div className="space-y-1.5 sm:col-span-2"><label htmlFor="connected-repository" className="text-xs font-medium">Connected repository</label><select id="connected-repository" className={inputClass} value={repositories.some((item) => item.full_name === repo) ? repo : ""} onChange={(event) => { const selected = repositories.find((item) => item.full_name === event.target.value); setRepo(selected?.full_name ?? ""); setRef(selected?.default_branch ?? "main"); setIssue(""); setIssues([]); }}><option value="">Choose a repository</option>{repositories.map((item) => <option key={item.id} value={item.full_name}>{item.full_name}{item.private ? " · private" : ""}</option>)}</select></div> : null}
      <div className="space-y-1.5"><label htmlFor="turn-repository" className="text-xs font-medium">Repository</label><input id="turn-repository" className={inputClass} value={continuation?.coding?.repository.repo_id ?? repo} onChange={(event) => { setRepo(event.target.value); setIssue(""); setIssues([]); }} placeholder="owner/repository" maxLength={140} required={!continuation} readOnly={!!continuation} autoCapitalize="none" spellCheck={false} /></div><div className="space-y-1.5"><label htmlFor="turn-ref" className="text-xs font-medium">Branch or commit</label><input id="turn-ref" className={inputClass} value={continuation?.coding?.repository.ref ?? ref} readOnly={!!continuation} onChange={(event) => setRef(event.target.value)} required={!continuation} maxLength={200} autoCapitalize="none" spellCheck={false} /></div>
      <div className="space-y-1.5"><label htmlFor="turn-issue" className="text-xs font-medium">Issue (optional)</label><input id="turn-issue" className={inputClass} type="number" min={1} max={2147483647} step={1} value={issue} onChange={(event) => setIssue(event.target.value)} list="available-issues" /><datalist id="available-issues">{issues.map((item) => <option key={item.number} value={item.number}>{item.title}</option>)}</datalist></div><div className="space-y-1.5"><label htmlFor="turn-tests" className="text-xs font-medium">Required tests</label><select id="turn-tests" className={inputClass} value={test} onChange={(event) => setTest(event.target.value)}><option value="python_pytest">Python · pytest</option><option value="python_unittest">Python · unittest</option><option value="node_test">Node · node --test</option><option value="node_npm_test">Node · npm test</option></select></div>
      <label className="flex items-center gap-2 text-xs sm:col-span-2"><input type="checkbox" className="size-4 accent-primary" checked={build} onChange={(event) => setBuild(event.target.checked)} />Also run npm build</label><p className="text-xs leading-5 text-muted-foreground sm:col-span-2">{continuation ? "This task continues the saved coding workspace." : "This task starts from the selected commit."} The server pins the repository revision. Code runs in an isolated E2B sandbox with preinstalled dependencies. Publishing needs a separate approval. <Link href="/connections" className="underline underline-offset-4">Manage GitHub</Link></p>{repoError ? <p className="text-xs text-muted-foreground sm:col-span-2">{repoError}</p> : null}
    </fieldset> : null}
    <div className="overflow-hidden rounded-2xl border bg-background focus-within:border-ring">
      <label htmlFor="conversation-goal" className="sr-only">{mode === "coding" ? "Describe your code change" : "Message AgentForge"}</label><textarea id="conversation-goal" value={goal} onChange={(event) => setGoal(event.target.value)} required minLength={1} maxLength={8000} disabled={locked} placeholder={archived ? "Restore this conversation to continue" : mode === "coding" ? "Describe the change and how you’ll know it works…" : mode === "research" ? "Ask a question. Tell your agents what evidence you need…" : "What would you like to work through?"} className="min-h-32 w-full resize-y bg-transparent px-5 pb-3 pt-5 text-sm leading-6 outline-none placeholder:text-muted-foreground disabled:opacity-60" />
      {attachments.length && mode !== "coding" ? <ul className="flex flex-wrap gap-2 px-4 pb-3">{attachments.map((item, index) => <li key={`${item.name}-${index}`} className="flex max-w-full items-center gap-2 rounded-md bg-muted px-2 py-1 text-xs"><FileText className="size-3.5 shrink-0" aria-hidden="true" /><span className="truncate">{item.name}</span><button type="button" className="rounded p-1 focus-visible:outline-2" aria-label={`Remove ${item.name}`} onClick={() => setAttachments((current) => current.filter((_, i) => i !== index))} disabled={locked}><X className="size-3" /></button></li>)}</ul> : null}
      <div className="flex flex-wrap items-center gap-2 px-3 pb-3"><Button type="button" variant="ghost" size="icon" aria-label="Attach text files" title="Attach text files" disabled={locked || mode === "coding"} onClick={() => fileInput.current?.click()}><Paperclip /></Button><input ref={fileInput} type="file" multiple accept=".txt,.md,.csv,.json,.py,.js,.jsx,.ts,.tsx,.html,.css,.yaml,.yml" className="hidden" tabIndex={-1} onChange={(event) => void attach(event.target.files)} />
        <label htmlFor="conversation-mode" className="sr-only">Task mode</label><select id="conversation-mode" value={mode} onChange={(event) => onModeChange(event.target.value as TaskMode)} disabled={locked} className="min-h-8 max-w-32 rounded-lg bg-muted/60 px-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring"><option value="research">Research</option><option value="general">General</option><option value="coding">Code</option></select>
        <span className="hidden min-w-0 max-w-48 truncate text-xs text-muted-foreground sm:block" title={config?.configured_model ?? config?.model ?? undefined}>{config?.configured_model ?? config?.model ?? "Model not configured"}</span>
        <div className="ml-auto flex items-center gap-1"><Button type="button" variant="ghost" size="icon" aria-label="Task budget and source settings" aria-expanded={settings} onClick={() => setSettings((value) => !value)} disabled={locked}><Settings2 /></Button><Button type="submit" size="icon" aria-label={busy ? "Sending message" : "Send message"} disabled={locked || !configured || goal.trim().length < 1}><ArrowUp /></Button></div>
      </div>
      {settings ? <fieldset disabled={locked} className="grid gap-4 border-t bg-muted/20 p-4 sm:grid-cols-2"><legend className="sr-only">Task limits and sources</legend><div className="space-y-1.5"><label htmlFor="turn-cost" className="text-xs font-medium">Maximum cost (USD)</label><input id="turn-cost" className={inputClass} type="number" min={0.0001} max={Number(config?.per_task_cost_limit_usd ?? 1)} step={0.0001} value={effectiveCost} onChange={(event) => setCost(event.target.value)} required /></div><div className="space-y-1.5"><label htmlFor="turn-seconds" className="text-xs font-medium">Time limit (seconds)</label><input id="turn-seconds" className={inputClass} type="number" min={30} max={1200} step={1} value={seconds} onChange={(event) => setSeconds(event.target.value)} required /></div>{mode !== "coding" ? <div className="space-y-1.5 sm:col-span-2"><label htmlFor="turn-hosts" className="text-xs font-medium">Restrict source domains (optional)</label><input id="turn-hosts" value={hosts} onChange={(event) => setHosts(event.target.value)} placeholder="example.org, docs.example.com" className={inputClass} /><p className="text-xs leading-5 text-muted-foreground">Up to 20 exact public domains. Leave empty to use sources found during this turn.</p></div> : null}{mode === "research" && agentOptions.length ? <div className="space-y-1.5 sm:col-span-2"><label htmlFor="turn-agent" className="text-xs font-medium">Research agent</label><select id="turn-agent" className={inputClass} value={agent} onChange={(event) => setAgent(event.target.value)}><option value="">Built-in researcher</option>{agentOptions.map((item) => <option key={item.key} value={item.key}>{item.name} · v{item.version}</option>)}</select></div> : null}<p className="text-xs leading-5 text-muted-foreground sm:col-span-2">Limits bound the whole turn. Conservative reserved costs may exceed measured provider usage.</p></fieldset> : null}
    </div>
    {mode === "general" ? <p className="text-xs leading-5 text-muted-foreground">General tasks analyze and draft text, read your full text attachments and create downloadable text files. {config?.general_capabilities?.includes("source_backed_research") ? "Web research is also configured." : "Web research is not configured."} Code execution and external actions are unavailable in this mode.</p> : null}
    {!configured && config ? <p className="text-xs leading-5 text-muted-foreground" role="status">{mode === "coding" ? "Coding" : mode === "general" ? "General tasks" : "Research"} setup is incomplete. {missing?.length ? `An operator must configure: ${missing.join(", ")}.` : "Ask the operator to check provider configuration."}</p> : null}
    {attachments.length && mode !== "coding" ? <p className="text-xs leading-5 text-muted-foreground">Up to four text files, 8,000 characters per file and 16 KB combined. Agents can read each file in bounded pages.</p> : null}
    {error ? <p role="alert" className="text-sm leading-6 text-destructive">{error}</p> : null}
    <p className="text-center text-[11px] leading-5 text-muted-foreground">{disabled ? "A turn is running. You can send another message when it finishes." : "1–8,000 characters · Agents can make mistakes. Review the evidence."}</p>
  </form>;
}
