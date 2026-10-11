"use client";
import Link from "next/link";
import Image from "next/image";
import { ATTACHMENT_ACCEPT, attachmentGoal, transferredFiles, uploadAttachment, validateFiles } from "@/lib/attachments";
import { useEffect, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { ArrowUp, FileText, LoaderCircle, Paperclip, Square, X } from "lucide-react";
import { ApprovalModePicker } from "@/features/sessions/approval-mode-picker";
import { iconButtonClass } from "@/features/sessions/composer-styles";
import { ContextStatusButton } from "@/features/sessions/context-status";
import { ModelPicker } from "@/features/sessions/model-picker";
import { RepositoryPicker } from "@/features/sessions/repository-picker";
import { estimateDraftTokens } from "@/lib/context";
import { useConversationDraft } from "@/features/sessions/use-conversation-draft";
import { canType, composerAction, isStopping, nextSubmission, runBlocksSending, sendBlock, type Submission } from "@/features/sessions/turn-flow";
import { errorAction, message } from "@/lib/task-api";
import { routes } from "@/lib/routes";
import { useEnterSends } from "@/lib/use-preferences";
import type { Attachment, RepositorySelection, TurnInput, WorkspaceConfiguration } from "@/lib/workspace-api";

/** The model shown last time, so the model control has its real width before the configuration arrives. */
const LAST_MODEL_KEY = "agentforge.last-model";
type ModelLabel = { id: string; provider: string | null };
function readLastModel(): ModelLabel | null {
  try { const value: unknown = JSON.parse(localStorage.getItem(LAST_MODEL_KEY) ?? "null"); return value && typeof value === "object" && typeof (value as ModelLabel).id === "string" ? value as ModelLabel : null; } catch { return null; }
}
function saveLastModel(model: ModelLabel | null) {
  try { if (model) localStorage.setItem(LAST_MODEL_KEY, JSON.stringify(model)); else localStorage.removeItem(LAST_MODEL_KEY); } catch { /* Only the first frame's width depends on it. */ }
}

const sendClass = "inline-flex size-11 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground outline-none transition-[background-color,color,transform] hover:bg-primary/85 focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:ring-offset-2 focus-visible:ring-offset-background active:scale-95 disabled:cursor-not-allowed disabled:bg-muted disabled:text-muted-foreground disabled:active:scale-100 motion-reduce:transition-none pointer-fine:sm:size-8";

/**
 * `below` renders under the composer without taking part in its layout (the empty chat's setup list), so what it shows,
 * and when, never moves the composer.
 */
export function ConversationComposer({ queue, repository, onRepositoryChange, config, disabled, repositoryLocked = false, running = false, runId = null, onStop, archived, onSend, menuSide = "top", sessionId = null, contextRevision = "new", onNewConversation, below, docked = false }: { queue?: ReactNode; sessionId?: string | null; docked?: boolean; contextRevision?: string; onNewConversation?: () => void; running?: boolean; runId?: string | null; onStop?: () => Promise<void>; menuSide?: "top" | "bottom"; repository: RepositorySelection | null; onRepositoryChange: (repository: RepositorySelection | null) => Promise<void>; config: WorkspaceConfiguration | null; disabled: boolean; repositoryLocked?: boolean; archived: boolean; onSend: (input: TurnInput) => Promise<void>; below?: ReactNode }) {
  const { goal, setGoal, attachments, setAttachments, clearDraft, flushDraft } = useConversationDraft(sessionId);
  // A conversation's layout (settings in the bottom row, no header) from the moment the composer docks under a thread, even
  // while that first message is still creating the conversation: the layout must not change once the thread opens.
  const conversation = !!sessionId || docked;
  const enterSends = useEnterSends();
  const [lastModel] = useState(readLastModel);
  const [error, setError] = useState("");
  // A link that fixes the send error on screen (for example "Reconnect GitHub"); it belongs to that error only.
  const [fix, setFix] = useState<{ label: string; href: string; error: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [repositoryBusy, setRepositoryBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const upload = useRef<AbortController | null>(null);
  useEffect(() => () => upload.current?.abort(), []);
  useEffect(() => {
    const preventFileNavigation = (event: DragEvent) => {
      if (Array.from(event.dataTransfer?.types ?? []).includes("Files")) event.preventDefault();
    };
    const reset = () => { dragDepth.current = 0; setDragging(false); };
    window.addEventListener("dragover", preventFileNavigation);
    window.addEventListener("drop", preventFileNavigation);
    window.addEventListener("drop", reset);
    window.addEventListener("dragend", reset);
    window.addEventListener("blur", reset);
    return () => {
      window.removeEventListener("dragover", preventFileNavigation);
      window.removeEventListener("drop", preventFileNavigation);
      window.removeEventListener("drop", reset);
      window.removeEventListener("dragend", reset);
      window.removeEventListener("blur", reset);
    };
  }, []);
  // The run a stop was requested for; the request lasts only as long as that run, so the next run's Stop button works again.
  const [stoppedRun, setStoppedRun] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const textarea = useRef<HTMLTextAreaElement>(null);
  // The idempotency key of the message being sent: kept after a failure, so an unchanged retry is the same turn on the server.
  const submission = useRef<Submission | null>(null);
  const actionPending = useRef(false);
  const stopping = isStopping(stoppedRun, runId, running);
  const configured = !!(config?.general_configured || config?.research_configured || config?.coding_configured);
  // Typing is only locked by what cannot take a message (loading, archived, a send in flight); a run in progress blocks sending, not typing.
  const locked = !canType({ disabled, archived, busy: busy || repositoryBusy });
  const blocked = sendBlock({ disabled, archived, busy: busy || repositoryBusy || uploading || stopping, runBlocks: runBlocksSending(running, config?.conversation_queue_enabled), configured, goal: attachmentGoal(goal, attachments) });
  const action = composerAction({ running, stopping, busy, blocked });
  const stopAction = action === "stop" || action === "stopping";
  const actionLabel = { send: "Send message", queue: "Queue message", stop: "Stop", stopping: "Stopping", sending: "Sending message" }[action];
  useEffect(() => {
    if (locked || !window.matchMedia("(pointer: fine)").matches || document.activeElement !== document.body) return;
    // Giving the field back after a send must not scroll: a focus scroll cancels the smooth scroll that is bringing the new turn to the top.
    textarea.current?.focus({ preventScroll: true });
  }, [locked]);
  // Sized before paint, so a new line never shows one frame at the old height.
  useLayoutEffect(() => {
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
    if (!running || !onStop || stopping || actionPending.current) return;
    actionPending.current = true;
    setStoppedRun(runId ?? "current"); setError("");
    try { await onStop(); }
    catch (error) { setStoppedRun(null); setError(message(error)); }
    finally { actionPending.current = false; }
  }
  async function attach(files: readonly File[]) {
    if (!files.length || locked) return;
    if (upload.current) { setError("Wait for the current files to finish attaching, then try again."); return; }
    setError("");
    const controller = new AbortController();
    upload.current = controller;
    setUploading(true);
    try {
      validateFiles(files, attachments.length);
      const additions: Attachment[] = [];
      const failures: string[] = [];
      for (const file of files) {
        try {
          const item = await uploadAttachment(file, controller.signal);
          if (controller.signal.aborted) return;
          const merged = [...attachments, ...additions, item];
          if (merged.reduce((sum, entry) => sum + new TextEncoder().encode(entry.content).length, 0) > 16000) {
            throw new Error(`${file.name}: combined attachment text exceeds 16 KB. Attach fewer text files.`);
          }
          additions.push(item);
        } catch (failure) {
          if (controller.signal.aborted) return;
          failures.push(message(failure));
        }
      }
      if (!controller.signal.aborted) {
        if (additions.length) setAttachments((current) => [...current, ...additions]);
        if (failures.length) setError(failures.join(" "));
      }
    } catch (failure) { if (!controller.signal.aborted) setError(message(failure)); }
    finally {
      if (!controller.signal.aborted) { upload.current = null; setUploading(false); }
      if (fileInput.current) fileInput.current.value = "";
    }
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); setError("");
    if (upload.current || stopping || actionPending.current) return;
    if (blocked !== null && blocked !== "empty") return;
    // No `plugins`: a first message's conversation starts with the plugins left on in Settings, chosen by the server when it is
    // created (a selection read here could be stale, or name a plugin since removed).
    const input: Omit<TurnInput, "idempotency_key"> = { routing: "auto", goal: attachmentGoal(goal, attachments), repository, allowed_web_hosts: [], attachments };
    if (input.goal.length < 1) { setError("Enter a message to send."); return; }
    submission.current = nextSubmission(submission.current, input, () => crypto.randomUUID());
    actionPending.current = true;
    setBusy(true);
    try { await onSend({ ...input, idempotency_key: submission.current.key }); await clearDraft().catch(() => {}); setGoal(""); setAttachments([]); submission.current = null; }
    catch (error) { const text = message(error); const action = errorAction(error); setError(text); setFix(action ? { ...action, error: text } : null); }
    finally { actionPending.current = false; setBusy(false); }
  }
  const modelId = config ? config.configured_model ?? config.model ?? null : lastModel?.id ?? null;
  const modelProvider = config ? config.configured_provider ?? config.provider ?? null : lastModel?.provider ?? null;
  useEffect(() => { if (config) saveLastModel(modelId ? { id: modelId, provider: modelProvider } : null); }, [config, modelId, modelProvider]);
  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.nativeEvent.isComposing || event.keyCode === 229) return;
    // With "Enter to send" off, Enter is a new line and Cmd/Ctrl+Enter sends; Cmd/Ctrl+Enter always sends.
    const sends = event.metaKey || event.ctrlKey || (enterSends && !event.shiftKey);
    if (!sends || blocked !== null) return;
    event.preventDefault(); event.currentTarget.form?.requestSubmit();
  }
  const form = <form onSubmit={submit} className="w-full space-y-2" aria-busy={busy || uploading} data-composer
    onDragEnter={(event) => { if (Array.from(event.dataTransfer.types).includes("Files")) { event.preventDefault(); dragDepth.current += 1; setDragging(true); } }}
    onDragOver={(event) => { if (Array.from(event.dataTransfer.types).includes("Files")) { event.preventDefault(); event.dataTransfer.dropEffect = locked || uploading ? "none" : "copy"; } }}
    onDragLeave={(event) => { event.preventDefault(); dragDepth.current = Math.max(0, dragDepth.current - 1); if (!dragDepth.current) setDragging(false); }}
    onDrop={(event) => { if (!Array.from(event.dataTransfer.types).includes("Files")) return; event.preventDefault(); dragDepth.current = 0; setDragging(false); void attach(transferredFiles(event.dataTransfer)); }}
    onPaste={(event) => {
      if (!event.currentTarget.contains(event.target as Node)) return;
      const files = transferredFiles(event.clipboardData);
      if (!files.length) return;
      event.preventDefault();
      const text = event.clipboardData.getData("text/plain");
      const field = textarea.current;
      if (text && field && !locked) setGoal((goal.slice(0, field.selectionStart) + text + goal.slice(field.selectionEnd)).slice(0, 8000));
      void attach(files);
    }}>
    <div>
      {!conversation ? <div data-composer-header className="mx-auto w-[calc(100%-2.5rem)] rounded-t-2xl border border-b-0 bg-muted px-2">
        <div role="group" aria-label="Conversation settings" className="flex min-h-11 items-center justify-between gap-2 pointer-fine:sm:min-h-10">
          <div className="min-w-0"><RepositoryPicker repository={repository} disabled={disabled || archived || busy || repositoryBusy || running || repositoryLocked} onChange={changeRepository} side={menuSide} /></div>
          <div className="shrink-0"><ApprovalModePicker side={menuSide} /></div>
        </div>
      </div> : null}
      {queue}
    <div className="relative rounded-3xl border bg-background shadow-[0_1px_2px_rgb(0_0_0/0.03),0_8px_24px_-14px_rgb(0_0_0/0.12)] transition-[border-color,box-shadow] focus-within:border-foreground/30 focus-within:shadow-[0_1px_2px_rgb(0_0_0/0.04),0_10px_28px_-14px_rgb(0_0_0/0.2)] motion-reduce:transition-none dark:bg-card dark:shadow-none dark:focus-within:shadow-none">
      {dragging ? <div className="pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-3xl border-2 border-dashed border-foreground/30 bg-background/95 text-sm text-foreground" role="status">{locked || uploading ? "Please wait before attaching files" : "Drop files to attach"}</div> : null}
      <label htmlFor="conversation-goal" className="sr-only">Message AgentForge</label><textarea ref={textarea} id="conversation-goal" rows={2} value={goal} onChange={(event) => setGoal(event.target.value)} required={!attachments.length} minLength={1} maxLength={8000} disabled={locked} onKeyDown={keyDown} onBlur={flushDraft} aria-keyshortcuts={enterSends ? "Enter" : "Meta+Enter Control+Enter"} placeholder={archived ? "Restore this conversation to continue" : "Ask anything, or describe what you want to build…"} className="block max-h-72 min-h-[3.75rem] w-full resize-none overflow-y-auto rounded-t-3xl bg-transparent px-5 pb-1 pt-4 text-[15px] leading-6 outline-none placeholder:text-muted-foreground disabled:opacity-60" />
      {attachments.length ? <ul className="flex flex-wrap gap-2 px-4 pb-1 pt-1">{attachments.map((item, index) => <li key={`${item.name}-${index}`} className="flex max-w-full items-center gap-2 rounded-full bg-muted py-1 pl-2.5 pr-1 text-xs">{item.media_type?.startsWith("image/") && item.upload_id ? <Image unoptimized src={`/api/v1/attachments/${item.upload_id}?sha256=${item.upload_sha256}`} alt="" width={28} height={28} className="size-7 shrink-0 rounded object-cover" /> : <FileText className="size-3.5 shrink-0" aria-hidden="true" />}<span className="truncate">{item.name}</span><button type="button" className="flex size-11 items-center justify-center rounded-full outline-none hover:bg-background focus-visible:ring-2 focus-visible:ring-ring/60 pointer-fine:sm:size-6" aria-label={`Remove ${item.name}`} onClick={() => setAttachments((current) => current.filter((_, i) => i !== index))} disabled={locked || uploading}><X className="size-3" /></button></li>)}</ul> : null}
      <div className="flex items-center justify-between gap-2 px-2 pb-2 pt-1">
        <div className="flex min-w-0 flex-1 items-center gap-0.5 sm:gap-1">
          <button type="button" className={iconButtonClass} aria-label="Attach files" title="Attach PDFs, images, or text files" disabled={locked || uploading} onClick={() => fileInput.current?.click()}><Paperclip className="size-4" aria-hidden="true" /></button>
          <input ref={fileInput} type="file" multiple accept={ATTACHMENT_ACCEPT} disabled={locked || uploading} className="hidden" tabIndex={-1} onChange={(event) => void attach(Array.from(event.target.files ?? []))} />
        </div>
        <div className="flex shrink-0 items-center gap-0.5 sm:gap-1">
          {conversation ? <ContextStatusButton sessionId={sessionId} revision={contextRevision} live={running} draftTokens={estimateDraftTokens(goal, attachments)} side="top" onNewConversation={onNewConversation} /> : null}
          {conversation ? <ApprovalModePicker side={menuSide} compact /> : null}
          <ModelPicker models={modelId ? [{ id: modelId, provider: modelProvider }] : []} value={modelId} loading={!config} disabled={locked} side={menuSide} />
          <button type={stopAction ? "button" : "submit"} className={sendClass} aria-label={actionLabel} title={actionLabel}
            disabled={stopAction ? stopping || !onStop : blocked !== null}
            onClick={stopAction ? () => void stop() : undefined}
            data-composer-action={action} data-composer-stop={stopAction || undefined} data-composer-send={!stopAction || undefined}>
            {busy || stopping ? <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden="true" />
              : stopAction ? <Square className="size-3.5 fill-current" aria-hidden="true" /> : <ArrowUp className="size-4" aria-hidden="true" />}
          </button>
        </div>
      </div>
    </div>
    </div>
    {error ? <p role="alert" className="text-xs leading-5 text-destructive">{error}{fix?.error === error ? <> <a href={fix.href} className="rounded font-medium underline underline-offset-2 outline-none focus-visible:ring-2 focus-visible:ring-ring/60">{fix.label}</a></> : null}</p> : null}
    {/* Said once when the state changes, never per token. */}
    <span role="status" className="sr-only">{stopping ? "Stopping the reply." : ""}</span>
    {/* Always exactly one line, whatever it says, so the configuration arriving never moves the composer. */}
    <p className="text-center text-[11px] leading-5 text-muted-foreground" role={config && !configured ? "status" : undefined}>{archived ? "Restore this conversation to send a message." : !config ? "Connecting to your workspace…" : !configured ? <>Connect a model before sending messages. <Link href={routes.settings("agent")} className="rounded underline underline-offset-2 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60">Settings → Agent</Link></> : blocked === "running" ? "A reply is in progress. Keep typing, or stop it to send now." : "Agents can make mistakes. Review important results."}</p>
  </form>;
  // The same root either way, so docking the composer under a thread (where nothing shows below it) never remounts the form.
  return <div className="relative w-full">{form}{below ? <div className="absolute inset-x-0 top-full pt-5">{below}</div> : null}</div>;
}
