"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowUp, FileText, Paperclip, X } from "lucide-react";
import { iconButtonClass } from "@/features/sessions/composer-styles";
import { ModelPicker } from "@/features/sessions/model-picker";
import { RepositoryPicker } from "@/features/sessions/repository-picker";
import { api, message, terminalStatuses, type Task, type CodingDetail } from "@/lib/task-api";
import type { Attachment, RepositorySelection, TurnInput, WorkspaceConfiguration } from "@/lib/workspace-api";

/** Run limits are managed by the platform; users never configure them. */
const defaultCostUsd = 0.5;
const defaultWallTimeSec = 300;

export function ConversationComposer({ repository, onRepositoryChange, config, disabled, archived, previousTaskId, onSend, menuSide = "top" }: { menuSide?: "top" | "bottom"; previousTaskId?: string | null; repository: RepositorySelection | null; onRepositoryChange: (repository: RepositorySelection | null) => Promise<void>; config: WorkspaceConfiguration | null; disabled: boolean; archived: boolean; onSend: (input: TurnInput) => Promise<void> }) {
  const [goal, setGoal] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [previousCoding, setPreviousCoding] = useState<Task | null>(null);
  const [continuationChoice, setContinuationChoice] = useState<{ key: string; checked: boolean } | null>(null);
  const continuationKey = `${previousTaskId ?? ""}:${repository?.repo_id ?? ""}:${repository?.ref ?? ""}`;
  const continuePrevious = continuationChoice?.key === continuationKey ? continuationChoice.checked : true;
  const repositoryId = repository?.repo_id;
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [repositoryBusy, setRepositoryBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const submission = useRef<{ fingerprint: string; key: string } | null>(null);
  const configured = !!(config?.general_configured || config?.research_configured || config?.coding_configured);
  const locked = disabled || archived || busy || repositoryBusy;
  const costLimit = Number(config?.per_task_cost_limit_usd ?? 1);
  const effectiveCost = String(Math.min(defaultCostUsd, costLimit));
  useEffect(() => {
    const field = textarea.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${field.scrollHeight}px`;
  }, [goal]);
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
    const input: Omit<TurnInput, "idempotency_key"> = { routing: "auto", goal: goal.trim(), repository, max_cost_usd: effectiveCost, max_wall_time_sec: defaultWallTimeSec, allowed_web_hosts: [], attachments, ...(repository && continuePrevious ? previousTaskId ? { continue_from_task_id: previousTaskId } : {} : { continue_from_task_id: null }) };
    if (input.goal.length < 1) { setError("Enter a message to send."); return; }
    const fingerprint = JSON.stringify(input);
    if (submission.current?.fingerprint !== fingerprint) submission.current = { fingerprint, key: crypto.randomUUID() };
    setBusy(true);
    try { await onSend({ ...input, idempotency_key: submission.current.key }); setGoal(""); setAttachments([]); submission.current = null; }
    catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }
  const showContinuation = !!repository && !!previousCoding && previousCoding.id === previousTaskId && previousCoding.coding?.repository.repo_id === repository.repo_id;
  const modelId = config?.configured_model ?? config?.model ?? null;
  const modelProvider = config?.configured_provider ?? config?.provider ?? null;
  return <form onSubmit={submit} className="w-full space-y-2" aria-busy={busy}>
    <div className="rounded-3xl border bg-background shadow-[0_1px_2px_rgb(0_0_0/0.03),0_8px_24px_-14px_rgb(0_0_0/0.12)] transition-[border-color,box-shadow] focus-within:border-foreground/30 focus-within:shadow-[0_1px_2px_rgb(0_0_0/0.04),0_10px_28px_-14px_rgb(0_0_0/0.2)] motion-reduce:transition-none">
      <label htmlFor="conversation-goal" className="sr-only">Message AgentForge</label><textarea ref={textarea} id="conversation-goal" rows={2} value={goal} onChange={(event) => setGoal(event.target.value)} required minLength={1} maxLength={8000} disabled={locked} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229 && !locked && configured && goal.trim()) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={archived ? "Restore this conversation to continue" : "Ask anything, or describe what you want to build…"} className="block max-h-72 min-h-[3.75rem] w-full resize-none overflow-y-auto rounded-t-3xl bg-transparent px-5 pb-1 pt-4 text-[15px] leading-6 outline-none placeholder:text-muted-foreground disabled:opacity-60" />
      {attachments.length ? <ul className="flex flex-wrap gap-2 px-4 pb-1 pt-1">{attachments.map((item, index) => <li key={`${item.name}-${index}`} className="flex max-w-full items-center gap-2 rounded-full bg-muted py-1 pl-2.5 pr-1 text-xs"><FileText className="size-3.5 shrink-0" aria-hidden="true" /><span className="truncate">{item.name}</span><button type="button" className="flex size-11 items-center justify-center rounded-full outline-none hover:bg-background focus-visible:ring-2 focus-visible:ring-ring/60 sm:size-6" aria-label={`Remove ${item.name}`} onClick={() => setAttachments((current) => current.filter((_, i) => i !== index))} disabled={locked}><X className="size-3" /></button></li>)}</ul> : null}
      <div className="flex items-center justify-between gap-2 px-2 pb-2 pt-1">
        <div className="flex min-w-0 flex-1 items-center gap-0.5 sm:gap-1">
          <button type="button" className={iconButtonClass} aria-label="Attach text files" title="Attach text files" disabled={locked} onClick={() => fileInput.current?.click()}><Paperclip className="size-4" aria-hidden="true" /></button>
          <input ref={fileInput} type="file" multiple accept=".txt,.md,.csv,.json,.py,.js,.jsx,.ts,.tsx,.html,.css,.yaml,.yml" className="hidden" tabIndex={-1} onChange={(event) => void attach(event.target.files)} />
          <RepositoryPicker repository={repository} disabled={disabled || archived || busy} onChange={changeRepository} side={menuSide} continuation={showContinuation ? { checked: continuePrevious, onChange: (checked) => setContinuationChoice({ key: continuationKey, checked }) } : undefined} />
        </div>
        <div className="flex shrink-0 items-center gap-0.5 sm:gap-1">
          <ModelPicker models={modelId ? [{ id: modelId, provider: modelProvider }] : []} value={modelId} loading={!config} disabled={locked} side={menuSide} />
          <button type="submit" className="inline-flex size-10 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground outline-none transition-[background-color,color,transform] hover:bg-primary/85 focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background active:scale-95 disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground disabled:active:scale-100 motion-reduce:transition-none sm:size-8" aria-label={busy ? "Sending message" : "Send message"} disabled={locked || !configured || !goal.trim()}><ArrowUp className="size-4" aria-hidden="true" /></button>
        </div>
      </div>
    </div>
    {!configured && config ? <p className="text-xs leading-5 text-muted-foreground" role="status">Your workspace needs a model connection before you can send messages. Check Settings.</p> : null}
    {error ? <p role="alert" className="text-xs leading-5 text-destructive">{error}</p> : null}
    <p className="text-center text-[11px] leading-5 text-muted-foreground">{archived ? "Restore this conversation to send a message." : repositoryBusy ? "Saving repository…" : busy ? "Sending…" : !config ? "Connecting to your workspace…" : disabled ? "A turn is running. You can send another message when it finishes." : "Agents can make mistakes. Review important results."}</p>
  </form>;
}
