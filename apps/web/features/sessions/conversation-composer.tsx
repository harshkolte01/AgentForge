"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowUp, FileText, Paperclip, Settings2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { inputClass } from "@/components/session-boundary";
import { RepositoryPicker } from "@/features/sessions/repository-picker";
import { api, message, terminalStatuses, type Task, type CodingDetail } from "@/lib/task-api";
import type { Attachment, RepositorySelection, TurnInput, WorkspaceConfiguration } from "@/lib/workspace-api";

export function ConversationComposer({ repository, onRepositoryChange, config, disabled, archived, previousTaskId, onSend }: { previousTaskId?: string | null; repository: RepositorySelection | null; onRepositoryChange: (repository: RepositorySelection | null) => Promise<void>; config: WorkspaceConfiguration | null; disabled: boolean; archived: boolean; onSend: (input: TurnInput) => Promise<void> }) {
  const [goal, setGoal] = useState("");
  const [cost, setCost] = useState("0.50");
  const [seconds, setSeconds] = useState("300");
  const [hosts, setHosts] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [previousCoding, setPreviousCoding] = useState<Task | null>(null);
  const [continuationChoice, setContinuationChoice] = useState<{ key: string; checked: boolean } | null>(null);
  const continuationKey = `${previousTaskId ?? ""}:${repository?.repo_id ?? ""}:${repository?.ref ?? ""}`;
  const continuePrevious = continuationChoice?.key === continuationKey ? continuationChoice.checked : true;
  const repositoryId = repository?.repo_id;
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [repositoryBusy, setRepositoryBusy] = useState(false);
  const [settings, setSettings] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const submission = useRef<{ fingerprint: string; key: string } | null>(null);
  const configured = !!(config?.general_configured || config?.research_configured || config?.coding_configured);
  const locked = disabled || archived || busy || repositoryBusy;
  const costLimit = Number(config?.per_task_cost_limit_usd ?? 1);
  const effectiveCost = Number(cost) > costLimit ? String(costLimit) : cost;
  useEffect(() => {
    if (!previousTaskId || !repositoryId) return;
    const controller = new AbortController();
    void (async () => {
      try {
        const task = await api<Task>(`/tasks/${previousTaskId}`, { signal: controller.signal });
        if (task.kind !== "coding" || task.coding?.repository.repo_id !== repositoryId || !terminalStatuses.has(task.status)) return;
        const detail = await api<CodingDetail>(`/tasks/${previousTaskId}/coding`, { signal: controller.signal });
        if (!controller.signal.aborted && detail.session) setPreviousCoding(task);
      } catch { /* Starting from the selected revision remains available. */ }
    })();
    return () => controller.abort();
  }, [previousTaskId, repositoryId]);
  async function changeRepository(value: RepositorySelection | null) {
    setRepositoryBusy(true);
    try { await onRepositoryChange(value); }
    finally { setRepositoryBusy(false); }
  }
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
    const input: Omit<TurnInput, "idempotency_key"> = { routing: "auto", goal: goal.trim(), repository, max_cost_usd: effectiveCost, max_wall_time_sec: Number(seconds), allowed_web_hosts: allowedHosts, attachments, ...(repository && continuePrevious ? previousTaskId ? { continue_from_task_id: previousTaskId } : {} : { continue_from_task_id: null }) };
    if (input.goal.length < 1 || allowedHosts.length > 20) { setError("Enter a message with up to 20 source domains."); return; }
    const fingerprint = JSON.stringify(input);
    if (submission.current?.fingerprint !== fingerprint) submission.current = { fingerprint, key: crypto.randomUUID() };
    setBusy(true);
    try { await onSend({ ...input, idempotency_key: submission.current.key }); setGoal(""); setAttachments([]); submission.current = null; }
    catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }
  return <form onSubmit={submit} className="w-full space-y-2" aria-busy={busy}>
    <div className="rounded-2xl border bg-background focus-within:border-ring">
      <label htmlFor="conversation-goal" className="sr-only">Message AgentForge</label><textarea id="conversation-goal" value={goal} onChange={(event) => setGoal(event.target.value)} required minLength={1} maxLength={8000} disabled={locked} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229 && !locked && configured && goal.trim()) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={archived ? "Restore this conversation to continue" : "Ask anything, or describe what you want to build…"} className="min-h-24 max-h-72 w-full resize-y rounded-t-2xl bg-transparent px-4 pb-2 pt-4 text-sm leading-6 outline-none placeholder:text-muted-foreground disabled:opacity-60" />
      {attachments.length ? <ul className="flex flex-wrap gap-2 px-4 pb-2">{attachments.map((item, index) => <li key={`${item.name}-${index}`} className="flex max-w-full items-center gap-2 rounded-md bg-muted px-2 py-1 text-xs"><FileText className="size-3.5 shrink-0" aria-hidden="true" /><span className="truncate">{item.name}</span><button type="button" className="flex size-11 items-center justify-center rounded focus-visible:outline-2 sm:size-6" aria-label={`Remove ${item.name}`} onClick={() => setAttachments((current) => current.filter((_, i) => i !== index))} disabled={locked}><X className="size-3" /></button></li>)}</ul> : null}
      <div className="px-2 pb-2"><div className="grid min-w-0 grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-1"><Button type="button" variant="ghost" size="icon" className="col-start-1 row-start-1 size-11 sm:size-8" aria-label="Attach text files" title="Attach text files" disabled={locked} onClick={() => fileInput.current?.click()}><Paperclip className="size-4" /></Button><input ref={fileInput} type="file" multiple accept=".txt,.md,.csv,.json,.py,.js,.jsx,.ts,.tsx,.html,.css,.yaml,.yml" className="hidden" tabIndex={-1} onChange={(event) => void attach(event.target.files)} /><RepositoryPicker repository={repository} disabled={locked} onChange={changeRepository} /><div className="col-start-3 row-start-1 ml-auto flex shrink-0 items-center gap-1"><Button type="button" variant="ghost" size="icon" className="size-11 sm:size-8" aria-label="Message settings" aria-expanded={settings} onClick={() => setSettings((value) => !value)} disabled={locked}><Settings2 className="size-4" /></Button><Button type="submit" size="icon" className="size-11 sm:size-8" aria-label={busy ? "Sending message" : "Send message"} disabled={locked || !configured || !goal.trim()}><ArrowUp className="size-4" /></Button></div></div></div>
      {settings ? <fieldset disabled={locked} className="grid gap-3 rounded-b-2xl border-t bg-muted/20 p-4 sm:grid-cols-2"><legend className="sr-only">Message limits and sources</legend><div className="space-y-1.5"><label htmlFor="turn-cost" className="text-xs font-medium">Maximum cost (USD)</label><input id="turn-cost" className={inputClass} type="number" min={0.0001} max={costLimit} step={0.0001} value={effectiveCost} onChange={(event) => setCost(event.target.value)} required /></div><div className="space-y-1.5"><label htmlFor="turn-seconds" className="text-xs font-medium">Time limit (seconds)</label><input id="turn-seconds" className={inputClass} type="number" min={30} max={1200} step={1} value={seconds} onChange={(event) => setSeconds(event.target.value)} required /></div><div className="space-y-1.5 sm:col-span-2"><label htmlFor="turn-hosts" className="text-xs font-medium">Source domains (optional)</label><input id="turn-hosts" value={hosts} onChange={(event) => setHosts(event.target.value)} placeholder="example.org, docs.example.com" className={inputClass} /><p className="text-xs leading-5 text-muted-foreground">Up to 20 exact public domains.</p></div>{repository && previousCoding && previousCoding.id === previousTaskId && previousCoding.coding?.repository.repo_id === repository.repo_id ? <label className="flex items-start gap-2 text-xs leading-5 sm:col-span-2"><input type="checkbox" checked={continuePrevious} onChange={(event) => setContinuationChoice({ key: continuationKey, checked: event.target.checked })} className="mt-0.5 size-4 accent-primary" /><span>Continue saved changes in this repository. Uncheck to start from the selected revision.</span></label> : null}</fieldset> : null}
    </div>
    {!configured && config ? <p className="text-xs leading-5 text-muted-foreground" role="status">Your workspace needs a model connection before you can send messages. Check Settings.</p> : null}
    {error ? <p role="alert" className="text-xs leading-5 text-destructive">{error}</p> : null}
    <p className="text-center text-[11px] leading-5 text-muted-foreground">{archived ? "Restore this conversation to send a message." : repositoryBusy ? "Saving repository…" : busy ? "Sending…" : !config ? "Connecting to your workspace…" : disabled ? "A turn is running. You can send another message when it finishes." : "Agents can make mistakes. Review important results."}</p>
  </form>;
}
