"use client";
import { useEffect, useId, useRef, useState } from "react";
import { ChevronDown, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, message } from "@/lib/task-api";
import type { ConversationSession, TurnResult } from "@/lib/workspace-api";

/** Mounted only for a nonempty queue and keyed by session, so previews never leak across conversations. */
export function ConversationQueue({ session, onChange }: { session: ConversationSession; onChange: (session: ConversationSession) => void }) {
  const [expanded, setExpanded] = useState(false);
  const listId = useId();
  const [turns, setTurns] = useState<TurnResult[] | null>(null);
  const [readError, setReadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const read = useRef<AbortController | null>(null);
  const action = useRef<AbortController | null>(null);

  useEffect(() => () => action.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    read.current = controller;
    void api<TurnResult[]>(`/sessions/${session.id}/turns?waiting=true`, { signal: controller.signal }).then((value) => {
      if (controller.signal.aborted) return;
      setTurns(value.filter((turn) => turn.state === "queued" || turn.state === "blocked"));
      setReadError("");
    }).catch((failed) => { if (!controller.signal.aborted) setReadError(message(failed)); });
    return () => controller.abort();
  }, [session.id, session.revision, session.queued_count, refresh]);

  async function act(turnId?: string) {
    if (action.current) return;
    const controller = new AbortController();
    action.current = controller;
    read.current?.abort();
    setBusy(turnId ?? "resume");
    setActionError("");
    try {
      const path = turnId ? `turns/${turnId}/cancel` : "queue/resume";
      const updated = await api<ConversationSession>(`/sessions/${session.id}/${path}`, { method: "POST", body: "{}", signal: controller.signal });
      if (controller.signal.aborted) return;
      if (turnId) setTurns((current) => current?.filter((turn) => turn.turn_id !== turnId) ?? null);
      onChange(updated);
    } catch (failed) {
      if (!controller.signal.aborted) setActionError(message(failed));
    } finally {
      if (!controller.signal.aborted) {
        // A read begun before the command completed must not put a cancelled message back.
        read.current?.abort();
        action.current = null;
        setBusy(null);
        setRefresh((value) => value + 1);
      }
    }
  }

  const first = turns?.[0];
  const controlClass = "inline-flex size-11 shrink-0 items-center justify-center rounded-md text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring disabled:opacity-40 pointer-fine:sm:size-7";


  return <section aria-label="Queued messages" data-composer-queue className="mx-auto w-[calc(100%-2.5rem)] rounded-t-xl border border-b-0 bg-muted/40 px-2 text-xs">
    <div className="flex min-h-11 items-center gap-1 pointer-fine:sm:min-h-9">
      <button type="button" aria-expanded={expanded} aria-controls={listId} aria-label={expanded ? "Collapse queued messages" : "Expand queued messages"} onClick={() => setExpanded((value) => !value)} className="flex min-h-11 min-w-0 flex-1 items-center gap-2 rounded-md px-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring pointer-fine:sm:min-h-9">
        <span className="shrink-0 text-muted-foreground">{session.queued_count} {session.queue_paused ? "paused" : "queued"}</span>
        {!expanded ? <span className="truncate text-foreground/80">{first?.user_message.content ?? (readError ? "Queue unavailable" : "Loading…")}</span> : null}
        <ChevronDown className={`ml-auto size-3 shrink-0 text-muted-foreground ${expanded ? "rotate-180" : ""}`} aria-hidden="true" />
      </button>
      {session.queue_paused ? <Button type="button" size="xs" variant="ghost" className="min-h-11 px-2 text-xs pointer-fine:sm:min-h-7" disabled={busy !== null || session.archived} onClick={() => void act()}>{busy === "resume" ? "Resuming…" : "Resume"}</Button> : null}
      {!expanded && first ? <button type="button" className={controlClass} disabled={busy !== null || !first.turn_id} aria-label={"Cancel queued message 1"} title="Cancel queued message" onClick={() => void act(first.turn_id)}><X className="size-3" aria-hidden="true" /></button> : null}
    </div>
    <div id={listId} hidden={!expanded}>
      {turns?.length ? <ol aria-label="Messages waiting to send" className="max-h-[min(9rem,25dvh)] overflow-y-auto overscroll-contain border-t border-border/60 py-1">
        {turns.map((turn, index) => <li key={turn.turn_id ?? turn.user_message.id} className="flex items-start gap-2 pl-2">
          <div className="min-w-0 flex-1 py-3 leading-5 pointer-fine:sm:py-1">
            <p className="whitespace-pre-wrap [overflow-wrap:anywhere]">{turn.user_message.content}</p>
            {turn.user_message.attachments?.length ? <p className="text-muted-foreground [overflow-wrap:anywhere]">Attachments: {turn.user_message.attachments.map((item) => item.name).join(", ")}</p> : null}
          </div>
          <button type="button" className={controlClass} disabled={busy !== null || !turn.turn_id} aria-label={`Cancel queued message ${index + 1}`} title="Cancel queued message" onClick={() => void act(turn.turn_id)}><X className="size-3" aria-hidden="true" /></button>
        </li>)}
      </ol> : !readError ? <p className="px-2 pb-2 text-muted-foreground">{turns === null ? "Loading queued messages…" : "Updating queue…"}</p> : null}
      {turns?.some((turn) => turn.state === "blocked" || turn.error_code) ? <p className="px-2 pb-2 leading-5 text-muted-foreground">{turns.some((turn) => turn.state === "blocked") ? "Waiting for access or limits to be resolved. Resume to retry." : "Waiting for task capacity."}</p> : null}
    </div>
    <span className="sr-only" role="status" aria-atomic="true">{session.queued_count} {session.queued_count === 1 ? "message" : "messages"} {session.queue_paused ? "paused" : "queued"}.</span>
    {readError ? <div className="flex items-center justify-between gap-2 px-2 pb-2"><p role="alert" className="min-w-0 text-destructive [overflow-wrap:anywhere]">Couldn’t refresh queued messages. {readError}</p><Button type="button" size="xs" variant="ghost" className="min-h-11 pointer-fine:sm:min-h-7" disabled={busy !== null} onClick={() => setRefresh((value) => value + 1)}>Retry</Button></div> : null}
    {actionError ? <p role="alert" className="px-2 pb-2 text-destructive [overflow-wrap:anywhere]">{actionError}</p> : null}
  </section>;
}
