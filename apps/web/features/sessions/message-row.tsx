"use client";
import { memo, useEffect, useState } from "react";
import { CircleAlert, FileText } from "lucide-react";
import { AnswerExtras } from "@/features/sessions/answer-extras";
import { AssistantMessage } from "@/features/sessions/assistant-message";
import { CopyButton } from "@/features/sessions/copy-button";
import { ActivityDisclosure } from "@/features/sessions/run-steps";
import type { Terminal } from "@/features/sessions/conversation-store";
import type { ActivityEntry } from "@/lib/activity/types";
import type { EntranceLedger } from "@/lib/entrance-ledger";
import { formatDate } from "@/lib/task-api";
import { actionCount } from "@/lib/tool-steps";
import type { ConversationMessage } from "@/lib/workspace-api";

/** Terminal receipts written by the server read better as plain sentences. */
export function readable(content: string) {
  const status = /^Task ended with status (\w+)\.$/.exec(content)?.[1];
  if (status === "CANCELLED") return "Stopped.";
  if (status === "FAILED") return "Something went wrong and I couldn't finish. Please try again.";
  return content;
}

export const actionsClass = "flex min-h-8 items-center gap-0.5 text-xs text-muted-foreground opacity-0 transition-opacity group-hover/message:opacity-100 group-focus-within/message:opacity-100 [@media(hover:none)]:opacity-100 motion-reduce:transition-none";
const userBubbleClass = "max-w-[85%] whitespace-pre-wrap break-words rounded-3xl bg-muted px-4 py-2.5 text-[15px] leading-7";

/**
 * Decides once, when the row first appears, whether it plays its entrance. The decision is kept for the row's life and the row is
 * marked as played, so a remount (the row moved to another list) or a re-render never plays it a second time.
 */
function useEntrance(ledger: EntranceLedger, rowKey: string, taskId: string | null, forced: boolean): string {
  const [animate] = useState(() => forced || ledger.shouldAnimate(rowKey, taskId));
  useEffect(() => { ledger.played(rowKey); }, [ledger, rowKey]);
  return animate ? "message-in" : "";
}

export function AttachmentChips({ attachments }: { attachments: readonly { name: string }[] | undefined }) {
  if (!attachments?.length) return null;
  return <ul aria-label="Attachments" className="mb-1.5 flex max-w-[85%] flex-wrap justify-end gap-1.5">
    {attachments.map((item, index) => <li key={`${item.name}-${index}`} className="inline-flex max-w-full items-center gap-1.5 rounded-full bg-muted py-1 pl-2 pr-2.5 text-xs text-muted-foreground"><FileText className="size-3.5 shrink-0" aria-hidden="true" /><span className="truncate">{item.name}</span></li>)}
  </ul>;
}

/**
 * Your message: shown at once as sent, and then as the message the server saved for it. The same row, so the same element: no
 * remount, no second entrance. `createdAt` is absent until the server has saved it.
 */
export const UserRow = memo(function UserRow({ ledger, rowKey, content, createdAt, attachments, pending = false, cancelled = false }: { ledger: EntranceLedger; rowKey: string; content: string; createdAt?: string; attachments?: readonly { name: string }[]; pending?: boolean; cancelled?: boolean }) {
  const entrance = useEntrance(ledger, rowKey, null, pending);
  const when = createdAt ? formatDate(createdAt) : "";
  return <article aria-label="Your message" data-message-row="user" data-scroll-anchor={rowKey} className={`group/message flex min-w-0 flex-col items-end ${entrance}`}>
    <AttachmentChips attachments={attachments} />
    <div className={userBubbleClass} title={when || undefined}>{content}</div>
    {cancelled ? <p className="text-xs text-muted-foreground">Cancelled before execution</p> : null}
    {/* Until it is saved the line is an empty placeholder, so saving it does not move anything below. */}
    {createdAt ? <div className={actionsClass}><time dateTime={createdAt} className="px-1.5">{when}</time><CopyButton text={content} label="Copy message" /></div> : <div className="min-h-8" aria-hidden="true" />}
  </article>;
});

/** One saved answer. Memoized on its (stable) record, so a streamed token or a refresh elsewhere never re-renders the history. */
export const AssistantRow = memo(function AssistantRow({ entry, ledger, ran, onRetry }: { entry: ConversationMessage; ledger: EntranceLedger; ran: Terminal | undefined; onRetry?: () => void }) {
  const entrance = useEntrance(ledger, entry.id, entry.task_id, false);
  const when = formatDate(entry.created_at);
  // Older servers send no `activity`: the capped list of finished calls stands in for the count.
  const legacyCount = entry.task_id ? actionCount(entry.steps ?? []) : 0;
  const failed = entry.activity?.status === "FAILED";
  return <article aria-label="Agent response" data-message-row="assistant" data-scroll-anchor={entry.task_id ? `task:${entry.task_id}` : entry.id} className={`group/message min-w-0 ${entrance}`}>
    {entry.task_id && ran ? <ActivityDisclosure taskId={entry.task_id} entries={ran.entries as ActivityEntry[]} durationMs={ran.durationMs} />
      : entry.task_id ? <ActivityDisclosure taskId={entry.task_id} activity={entry.activity} legacyCount={legacyCount} /> : null}
    {failed ? <p className="flex items-start gap-2 whitespace-pre-wrap break-words text-[15px] leading-7 text-destructive"><CircleAlert className="mt-1.5 size-4 shrink-0" aria-hidden="true" />{readable(entry.content)}</p>
      : <AssistantMessage content={readable(entry.content)} />}
    {failed && onRetry ? <button type="button" onClick={onRetry} className="mt-2 inline-flex min-h-8 items-center rounded-lg border px-3 text-[13px] font-medium text-foreground outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/60 motion-reduce:transition-none [@media(pointer:coarse)]:min-h-11">Try again</button> : null}
    {entry.task_id ? <AnswerExtras taskId={entry.task_id} activity={entry.activity} /> : null}
    <div className={`${actionsClass} -ml-2 mt-1`}>
      <CopyButton text={entry.content} label="Copy response" />
      <time dateTime={entry.created_at} className="px-1.5">{when}</time>
    </div>
  </article>;
});
