"use client";
import { useEffect, useId, useRef, useState } from "react";
import { ArrowRight, ChevronRight, GitBranch, Sparkles, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { useDeliveryContext } from "@/features/delivery/delivery-context";
import { inputClass, smallButton } from "@/features/delivery/styles";
import { createPullRequest, pushChanges, suggestPublication } from "@/lib/delivery-api";
import { deliveryMessage } from "@/lib/delivery-errors";
import { gateAction } from "@/lib/delivery-status";

type Fields = { message: string; title: string; body: string };
type Field = keyof Fields;

/**
 * Publishing, kept small: a title and an optional description for the pull request (the first time), and a commit message that
 * follows the title until you open it and write your own. They are drafted from the diff and the conversation by
 * `POST /delivery/suggest`, which never blocks: until it answers the fields are editable, and anything you type is kept.
 * Opens a pull request the first time and pushes an update afterwards.
 */
export function PublishDialog() {
  const delivery = useDeliveryContext();
  const id = useId();
  const [fields, setFields] = useState<Fields>({ message: "", title: "", body: "" });
  const [draft, setDraft] = useState(true);
  const [showMessage, setShowMessage] = useState(false);
  const [suggested, setSuggested] = useState<{ status: "loading" | "ready" | "failed"; branch: string | null }>({ status: "loading", branch: null });
  const [attempt, setAttempt] = useState(0);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const touched = useRef(new Set<Field>());
  const first = useRef<HTMLElement | null>(null);
  const sessionId = delivery?.sessionId;

  useEffect(() => {
    if (!sessionId) return;
    const controller = new AbortController();
    void suggestPublication(sessionId, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(30_000)]) }).then((suggestion) => {
      if (controller.signal.aborted) return;
      const fill = (field: Field, value: string) => (touched.current.has(field) ? undefined : value);
      setFields((current) => ({ message: fill("message", suggestion.commit_message) ?? current.message, title: fill("title", suggestion.pr_title) ?? current.title, body: fill("body", suggestion.pr_body) ?? current.body }));
      setSuggested({ status: "ready", branch: suggestion.branch || null });
    }).catch(() => { if (!controller.signal.aborted) setSuggested((current) => ({ ...current, status: "failed" })); });
    return () => controller.abort();
  }, [sessionId, attempt]);

  if (!delivery || !delivery.data.view) return null;
  const { data, summary, closePublish } = delivery;
  const view = data.view!;
  const pr = view.pull_request;
  const creating = !pr;
  const unpublished = summary?.files ?? 0;
  const needsMessage = unpublished > 0;
  const gate = gateAction(creating ? "create_pull_request" : "push_update", view, data.changes);
  const branch = view.branch ?? suggested.branch;
  const set = (field: Field, value: string) => {
    touched.current.add(field);
    // Until you write a commit message of your own, it is the title.
    setFields((current) => ({ ...current, [field]: value, ...(field === "title" && !touched.current.has("message") ? { message: value } : {}) }));
  };
  // The sandbox's own list says whether any file is blocked, so publishing waits for it to be read.
  const reading = needsMessage && !data.changes;
  const ready = !reading && (!needsMessage || !!fields.message.trim()) && (!creating || !!fields.title.trim());
  // The branch name is sent back on the first publish only; once a branch exists the server ignores it.
  const newBranch = !view.branch && suggested.branch ? suggested.branch : undefined;
  const suggesting = suggested.status === "loading";
  const messageOpen = showMessage || !creating;

  async function submit() {
    if (!delivery || !ready || !gate.enabled || submitting) return;
    const message = fields.message.trim(), title = fields.title.trim(), body = fields.body;
    setSubmitting(true); setError("");
    const outcome = creating
      ? await data.run("create_pull_request", `pr:${draft}:${title}:${body}:${message}`, (key) => createPullRequest(delivery.sessionId, { idempotency_key: key, title, body, draft, message: needsMessage ? message : undefined, branch: newBranch }))
      : await data.run("push_update", `push:${message}`, (key) => pushChanges(delivery.sessionId, { idempotency_key: key, message, branch: newBranch }));
    setSubmitting(false);
    // The failure is shown here, beside the fields; the panel's own banner would only repeat it.
    data.dismissFailure();
    if (outcome.ok) { closePublish(); delivery.openPanel(creating ? "pull_request" : "changes"); }
    else setError(deliveryMessage(outcome.error));
  }

  const note = !gate.enabled && gate.reason ? `${gate.reason}${/…$/.test(gate.reason) ? "" : "."}`
    : reading ? "Checking your changes…" : suggested.status === "failed" ? "Couldn't draft this for you. Fill it in yourself." : "";
  return <Dialog open onOpenChange={(open) => { if (!open && !submitting) closePublish(); }}>
    <DialogContent initialFocus={(type) => (type === "touch" ? true : first.current ?? true)}>
      <form onSubmit={(event) => { event.preventDefault(); void submit(); }} className="flex min-h-0 flex-1 flex-col">
        <div className="flex items-start justify-between gap-3 px-5 pb-1 pt-4">
          <div className="min-w-0 space-y-1.5">
            <DialogTitle>{creating ? "Create pull request" : "Push update"}</DialogTitle>
            <DialogDescription className="flex min-w-0 items-center gap-1.5 font-mono text-xs">
              <GitBranch className="size-3.5 shrink-0" aria-hidden="true" />
              <span className="min-w-0 truncate" title={branch ?? undefined}>{branch ?? "new branch"}</span>
              <ArrowRight className="size-3 shrink-0" aria-hidden="true" />
              <span className="shrink-0">{creating ? view.base_branch ?? "base" : `#${pr?.number}`}</span>
            </DialogDescription>
          </div>
          <Button type="button" variant="ghost" size="icon-sm" className="-mr-2 size-8 shrink-0 pointer-coarse:size-11" aria-label="Close" disabled={submitting} onClick={closePublish}><X className="size-4" /></Button>
        </div>
        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto overscroll-contain px-5 pb-4 pt-3">
          {creating ? <>
            <input ref={(node) => { first.current = node; }} aria-label="Title" value={fields.title} onChange={(event) => set("title", event.target.value)} maxLength={256} autoComplete="off" required
              className={`${inputClass} text-[15px] font-medium`} placeholder={suggesting ? "Drafting a title…" : "Title"} />
            <div className="relative">
              <textarea aria-label="Description" value={fields.body} onChange={(event) => set("body", event.target.value)} rows={5} className={`${inputClass} resize-none pb-8 font-mono text-[13px] leading-5`} placeholder={suggesting ? "Drafting a description…" : "Description (optional)"} />
              <button type="button" aria-label="Rewrite from my changes" title="Rewrite from my changes" disabled={suggesting || submitting}
                onClick={() => { touched.current.clear(); setSuggested({ status: "loading", branch: suggested.branch }); setAttempt((value) => value + 1); }}
                className="absolute bottom-1.5 right-1.5 inline-flex size-6 items-center justify-center rounded-md text-muted-foreground outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 disabled:pointer-events-none disabled:opacity-40 motion-reduce:transition-none pointer-coarse:size-9">
                <Sparkles className={`size-3.5 ${suggesting ? "animate-pulse motion-reduce:animate-none" : ""}`} aria-hidden="true" /></button>
            </div>
          </> : null}
          {needsMessage ? (creating && !messageOpen
            ? <button type="button" aria-expanded={false} aria-controls={`${id}-message`} onClick={() => setShowMessage(true)} className="inline-flex min-h-6 items-center gap-1 rounded text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 pointer-coarse:min-h-11"><ChevronRight className="size-3.5" aria-hidden="true" />Commit message</button>
            : <div className="space-y-1.5">
              {creating ? <button type="button" aria-expanded onClick={() => setShowMessage(false)} className="inline-flex min-h-6 items-center gap-1 rounded text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 pointer-coarse:min-h-11"><ChevronRight className="size-3.5 rotate-90" aria-hidden="true" />Commit message</button> : null}
              <textarea id={`${id}-message`} aria-label="Commit message" ref={creating ? undefined : (node) => { first.current = node; }} value={fields.message} onChange={(event) => set("message", event.target.value)} rows={2} required className={`${inputClass} resize-none`} placeholder={suggesting ? "Drafting a commit message…" : "Describe this commit"} />
            </div>) : null}
        </div>
        <div className="flex flex-col gap-2 border-t px-5 py-3">
          {error ? <p role="alert" className="text-xs leading-5 text-destructive">{error}</p> : note ? <p role="status" aria-live="polite" className="text-xs leading-5 text-muted-foreground">{note}</p> : null}
          <div className="flex items-center gap-2">
            {creating ? <label className="mr-auto inline-flex min-h-8 cursor-pointer items-center gap-2 text-[13px] text-muted-foreground pointer-coarse:min-h-11">
              <input type="checkbox" checked={draft} onChange={(event) => setDraft(event.target.checked)} className="size-4 accent-foreground" />Draft</label> : <span className="mr-auto" />}
            <Button type="button" variant="ghost" className={`min-h-10 px-3 sm:min-h-8 ${smallButton}`} disabled={submitting} onClick={closePublish}>Cancel</Button>
            <Button type="submit" className={`min-h-10 px-3 sm:min-h-8 ${smallButton}`} disabled={!ready || !gate.enabled || submitting}>{submitting ? (creating ? "Creating…" : "Pushing…") : creating ? "Create pull request" : "Push update"}</Button>
          </div>
        </div>
      </form>
    </DialogContent>
  </Dialog>;
}
