"use client";
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { ArrowUp, FileText, LoaderCircle, Paperclip, Square, X } from "lucide-react";
import { ApprovalModePicker } from "@/features/sessions/approval-mode-picker";
import { iconButtonClass } from "@/features/sessions/composer-styles";
import { ContextStatusButton } from "@/features/sessions/context-status";
import { ModelPicker } from "@/features/sessions/model-picker";
import { RepositoryPicker } from "@/features/sessions/repository-picker";
import { estimateDraftTokens } from "@/lib/context";
import { useConversationDraft } from "@/features/sessions/use-conversation-draft";
import { canType, isStopping, nextSubmission, runBlocksSending, sendBlock, type Submission } from "@/features/sessions/turn-flow";
import { message } from "@/lib/task-api";
import type { Attachment, RepositorySelection, TurnInput, WorkspaceConfiguration } from "@/lib/workspace-api";

const sendClass = "inline-flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground outline-none transition-[background-color,color,transform] hover:bg-primary/85 focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background active:scale-95 disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground disabled:active:scale-100 motion-reduce:transition-none pointer-fine:sm:size-8";

export function ConversationComposer({ queue, repository, onRepositoryChange, config, disabled, repositoryLocked = false, running = false, runId = null, onStop, archived, onSend, menuSide = "top", sessionId = null, contextRevision = "new", onNewConversation }: { queue?: ReactNode; sessionId?: string | null; contextRevision?: string; onNewConversation?: () => void; running?: boolean; runId?: string | null; onStop?: () => Promise<void>; menuSide?: "top" | "bottom"; repository: RepositorySelection | null; onRepositoryChange: (repository: RepositorySelection | null) => Promise<void>; config: WorkspaceConfiguration | null; disabled: boolean; repositoryLocked?: boolean; archived: boolean; onSend: (input: TurnInput) => Promise<void> }) {
  const { goal, setGoal, attachments, setAttachments, clearDraft } = useConversationDraft(sessionId);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [repositoryBusy, setRepositoryBusy] = useState(false);
  // The run a stop was requested for; the request lasts only as long as that run, so the next run's Stop button works again.
  const [stoppedRun, setStoppedRun] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  // The idempotency key of the message being sent: kept after a failure, so an unchanged retry is the same turn on the server.
  const submission = useRef<Submission | null>(null);
  const stopping = isStopping(stoppedRun, runId, running);
  const configured = !!(config?.general_configured || config?.research_configured || config?.coding_configured);
  // Typing is only locked by what cannot take a message (loading, archived, a send in flight); a run in progress blocks sending, not typing.
  const locked = !canType({ disabled, archived, busy: busy || repositoryBusy });
  const blocked = sendBlock({ disabled, archived, busy: busy || repositoryBusy, runBlocks: runBlocksSending(running, config?.conversation_queue_enabled), configured, goal });
  useEffect(() => {
    if (locked || !window.matchMedia("(pointer: fine)").matches || document.activeElement !== document.body) return;
    textarea.current?.focus();
  }, [locked]);
  useEffect(() => {
    const field = textarea.current;
    if (!field) return;
    field.style.height = "auto";
    field.style.height = `${field.scrollHeight}px`;
  }, [goal]);
  async function changeRepository(value: RepositorySelection | null) {
    setRepositoryBusy(true);
    try { await onRepositoryChange(value); }
    finally { setRepositoryBusy(false); }
  }
  async function stop() {
    if (!onStop || stopping) return;
    setStoppedRun(runId ?? "current"); setError("");
    try { await onStop(); }
    catch (error) { setStoppedRun(null); setError(message(error)); }
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
    if (blocked !== null && blocked !== "empty") return;
    const input: Omit<TurnInput, "idempotency_key"> = { routing: "auto", goal: goal.trim(), repository, allowed_web_hosts: [], attachments };
    if (input.goal.length < 1) { setError("Enter a message to send."); return; }
    submission.current = nextSubmission(submission.current, input, () => crypto.randomUUID());
    setBusy(true);
    try { await onSend({ ...input, idempotency_key: submission.current.key }); await clearDraft().catch(() => {}); setGoal(""); setAttachments([]); submission.current = null; }
    catch (error) { setError(message(error)); }
    finally { setBusy(false); }
  }
  const modelId = config?.configured_model ?? config?.model ?? null;
  const modelProvider = config?.configured_provider ?? config?.provider ?? null;
  return <form onSubmit={submit} className="w-full space-y-2" aria-busy={busy} data-composer>
    <div>
      {!sessionId ? <div data-composer-header className="mx-auto w-[calc(100%-2.5rem)] rounded-t-2xl border border-b-0 bg-muted px-2">
        <div role="group" aria-label="Conversation settings" className="flex min-h-11 items-center justify-between gap-2 pointer-fine:sm:min-h-10">
          <div className="min-w-0"><RepositoryPicker repository={repository} disabled={disabled || archived || busy || repositoryBusy || running || repositoryLocked} onChange={changeRepository} side={menuSide} /></div>
          <div className="shrink-0"><ApprovalModePicker side={menuSide} /></div>
        </div>
      </div> : null}
      {queue}
    <div className="relative rounded-3xl border bg-background shadow-[0_1px_2px_rgb(0_0_0/0.03),0_8px_24px_-14px_rgb(0_0_0/0.12)] transition-[border-color,box-shadow] focus-within:border-foreground/30 focus-within:shadow-[0_1px_2px_rgb(0_0_0/0.04),0_10px_28px_-14px_rgb(0_0_0/0.2)] motion-reduce:transition-none">
      <label htmlFor="conversation-goal" className="sr-only">Message AgentForge</label><textarea ref={textarea} id="conversation-goal" rows={2} value={goal} onChange={(event) => setGoal(event.target.value)} required minLength={1} maxLength={8000} disabled={locked} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.keyCode !== 229 && blocked === null) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={archived ? "Restore this conversation to continue" : "Ask anything, or describe what you want to build…"} className="block max-h-72 min-h-[3.75rem] w-full resize-none overflow-y-auto rounded-t-3xl bg-transparent px-5 pb-1 pt-4 text-[15px] leading-6 outline-none placeholder:text-muted-foreground disabled:opacity-60" />
      {attachments.length ? <ul className="flex flex-wrap gap-2 px-4 pb-1 pt-1">{attachments.map((item, index) => <li key={`${item.name}-${index}`} className="flex max-w-full items-center gap-2 rounded-full bg-muted py-1 pl-2.5 pr-1 text-xs"><FileText className="size-3.5 shrink-0" aria-hidden="true" /><span className="truncate">{item.name}</span><button type="button" className="flex size-11 items-center justify-center rounded-full outline-none hover:bg-background focus-visible:ring-2 focus-visible:ring-ring/60 pointer-fine:sm:size-6" aria-label={`Remove ${item.name}`} onClick={() => setAttachments((current) => current.filter((_, i) => i !== index))} disabled={locked}><X className="size-3" /></button></li>)}</ul> : null}
      <div className="flex items-center justify-between gap-2 px-2 pb-2 pt-1">
        <div className="flex min-w-0 flex-1 items-center gap-0.5 sm:gap-1">
          <button type="button" className={iconButtonClass} aria-label="Attach text files" title="Attach text files" disabled={locked} onClick={() => fileInput.current?.click()}><Paperclip className="size-4" aria-hidden="true" /></button>
          <input ref={fileInput} type="file" multiple accept=".txt,.md,.csv,.json,.py,.js,.jsx,.ts,.tsx,.html,.css,.yaml,.yml" className="hidden" tabIndex={-1} onChange={(event) => void attach(event.target.files)} />
        </div>
        <div className="flex shrink-0 items-center gap-0.5 sm:gap-1">
          {sessionId ? <ContextStatusButton sessionId={sessionId} revision={contextRevision} live={running} draftTokens={estimateDraftTokens(goal, attachments)} side="top" onNewConversation={onNewConversation} /> : null}
          {sessionId ? <ApprovalModePicker side={menuSide} compact /> : null}
          <ModelPicker models={modelId ? [{ id: modelId, provider: modelProvider }] : []} value={modelId} loading={!config} disabled={locked} side={menuSide} />
          {running ? <button type="button" className={sendClass} aria-label={stopping ? "Stopping" : "Stop"} title={stopping ? "Stopping" : "Stop"} disabled={stopping || !onStop} onClick={() => void stop()} data-composer-stop><Square className="size-3.5 fill-current" aria-hidden="true" /></button> : null}
          <button type="submit" className={sendClass} aria-label={busy ? "Sending message" : "Send message"} disabled={blocked !== null} data-composer-send>{busy ? <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden="true" /> : <ArrowUp className="size-4" aria-hidden="true" />}</button>
        </div>
      </div>
    </div>
    </div>
    {!configured && config ? <p className="text-xs leading-5 text-muted-foreground" role="status">Your workspace needs a model connection before you can send messages. Check Settings.</p> : null}
    {error ? <p role="alert" className="text-xs leading-5 text-destructive">{error}</p> : null}
    {/* Said once when the state changes, never per token. */}
    <span role="status" className="sr-only">{stopping ? "Stopping the reply." : ""}</span>
    <p className="text-center text-[11px] leading-5 text-muted-foreground">{archived ? "Restore this conversation to send a message." : !config ? "Connecting to your workspace…" : blocked === "running" ? "A reply is in progress. Keep typing, or stop it to send now." : "Agents can make mistakes. Review important results."}</p>
  </form>;
}
