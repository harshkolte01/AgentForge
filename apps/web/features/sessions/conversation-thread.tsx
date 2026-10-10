"use client";
import { useEffect, useRef, useState } from "react";
import { AssistantMessage } from "@/features/sessions/assistant-message";
import { TaskTurn } from "@/features/sessions/task-turn";
import { Button } from "@/components/ui/button";
import { api, message, formatDate } from "@/lib/task-api";
import type { ConversationMessage, ConversationSession } from "@/lib/workspace-api";

export function ConversationThread({ session, refresh, onChange }: { session: ConversationSession; refresh: number; onChange: (session: ConversationSession) => void }) {
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [olderBusy, setOlderBusy] = useState(false);
  const [finished, setFinished] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const records = useRef<ConversationMessage[]>([]);
  const changeCallback = useRef(onChange);
  useEffect(() => { changeCallback.current = onChange; }, [onChange]);
  useEffect(() => {
    const controller = new AbortController();
    void (async () => {
      try {
        const next = await api<ConversationSession>(`/sessions/${session.id}`, { signal: controller.signal });
        let cursor = records.current.length ? records.current[records.current.length - 1].sequence : Math.max(0, next.last_sequence - 100);
        const additions: ConversationMessage[] = [];
        for (let page = 0; page < 4; page++) {
          const batch = await api<ConversationMessage[]>(`/sessions/${session.id}/messages?after_sequence=${cursor}&limit=100`, { signal: controller.signal });
          additions.push(...batch);
          if (batch.length < 100) break;
          cursor = batch[batch.length - 1].sequence;
        }
        if (!controller.signal.aborted) {
          const scroller = root.current?.closest<HTMLElement>("[data-conversation-scroll]");
          const follow = !records.current.length || !scroller || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 180;
          records.current = [...new Map([...records.current, ...additions].map((entry) => [entry.sequence, entry])).values()].sort((a, b) => a.sequence - b.sequence);
          setMessages(records.current); changeCallback.current(next);
          setError(""); setLoading(false);
          if (follow) requestAnimationFrame(() => { if (!controller.signal.aborted) bottom.current?.scrollIntoView({ behavior: "instant", block: "end" }); });
        }
      } catch (error) { if (!controller.signal.aborted) { setError(message(error)); setLoading(false); } }
    })();
    return () => controller.abort();
  }, [session.id, refresh, finished]);
  async function loadOlder() {
    const first = records.current[0]; if (!first || first.sequence <= 1) return;
    setOlderBusy(true); setError("");
    const scroller = root.current?.closest<HTMLElement>("[data-conversation-scroll]");
    const height = scroller?.scrollHeight ?? 0;
    const top = scroller?.scrollTop ?? 0;
    try {
      const batch = await api<ConversationMessage[]>(`/sessions/${session.id}/messages?after_sequence=${Math.max(0, first.sequence - 101)}&limit=100`);
      records.current = [...new Map([...batch, ...records.current].map((entry) => [entry.sequence, entry])).values()].sort((a, b) => a.sequence - b.sequence);
      setMessages(records.current);
      requestAnimationFrame(() => { if (scroller) scroller.scrollTop = top + scroller.scrollHeight - height; });
    } catch (error) { setError(message(error)); } finally { setOlderBusy(false); }
  }
  const displayedTasks = new Set<string>();
  return <div ref={root} className="mx-auto w-full max-w-3xl space-y-6 px-5 pb-4 pt-6 sm:px-8">
    {loading ? <p role="status" className="text-sm text-muted-foreground">Loading conversation…</p> : null}
    {messages[0]?.sequence > 1 ? <div className="text-center"><Button variant="ghost" size="sm" disabled={olderBusy} onClick={() => void loadOlder()}>{olderBusy ? "Loading earlier messages…" : "Load earlier messages"}</Button></div> : null}
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    {messages.map((entry) => {
      const showTask = entry.task_id && !displayedTasks.has(entry.task_id);
      if (entry.task_id) displayedTasks.add(entry.task_id);
      return <article key={entry.id} className="min-w-0 space-y-1" aria-label={entry.role === "user" ? "Your message" : "Agent response"}>
        <div className={entry.role === "user" ? "ml-auto max-w-[92%] rounded-xl bg-muted/65 px-4 py-3 sm:max-w-[85%]" : "space-y-3 py-1"}>
          {entry.role === "assistant" ? <p className="text-xs font-medium text-muted-foreground">AgentForge</p> : null}
          {entry.role === "assistant" ? <AssistantMessage content={entry.content} /> : <p className="whitespace-pre-wrap break-words text-sm leading-6">{entry.content}</p>}<time dateTime={entry.created_at} className="mt-2 block text-[11px] text-muted-foreground">{formatDate(entry.created_at)}</time>
        </div>
        {showTask ? <TaskTurn taskId={entry.task_id!} active={entry.task_id === session.active_task_id} onFinished={() => setFinished((value) => value + 1)} /> : null}
      </article>;
    })}
    {!loading && !messages.length ? <p className="py-8 text-center text-sm leading-6 text-muted-foreground">This conversation is ready. Send a message to start a task.</p> : null}
    <div ref={bottom} />
  </div>;
}
