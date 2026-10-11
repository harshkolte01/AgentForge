"use client";
import { useId, useState } from "react";
import { ShieldQuestion } from "lucide-react";
import { Button } from "@/components/ui/button";
import { approvalTitle, detailSize, type PendingApproval } from "@/lib/activity/approvals";
import { answerApproval } from "@/lib/approvals-api";
import { message } from "@/lib/task-api";

/**
 * The agent stopped before something that changes something and is waiting for an answer. The card goes away when the run's
 * `approval.decided` event arrives, so it never claims an answer the server has not recorded.
 *
 * The detail is exactly what will run (a whole command, a file's full content, an edit's old and new text, a pull request's
 * body), so it can be long: a short one shows as is; a long one says its size, scrolls in its own box and can be expanded to
 * read more at once. When the server had to cut it short, the card says so, so nobody approves text they could not see.
 */
export function ApprovalCard({ approval }: { approval: PendingApproval }) {
  const [sent, setSent] = useState<"approve" | "deny" | null>(null);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState(false);
  const detailId = useId();
  async function answer(decision: "approve" | "deny") {
    setSent(decision); setError("");
    try { await answerApproval(approval.id, decision); }
    catch (failure) { setSent(null); setError(message(failure)); }
  }
  const title = approvalTitle(approval.tool);
  const size = detailSize(approval.detail);
  return <div role="group" aria-label={title} data-approval-card className="message-in flex min-w-0 flex-col gap-2.5 rounded-2xl border bg-background p-3.5 shadow-[0_1px_2px_rgb(0_0_0/0.03)] dark:bg-card dark:shadow-none">
    <p className="flex items-center gap-2 text-[13px] font-medium leading-5 text-foreground"><ShieldQuestion className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />{title}</p>
    {approval.detail ? <pre id={detailId} tabIndex={size ? 0 : undefined} role={size ? "region" : undefined} aria-label={size ? "Exact action" : undefined} className={`${expanded ? "max-h-72" : "max-h-44"} min-w-0 overflow-auto overscroll-contain whitespace-pre-wrap break-words rounded-lg bg-muted px-3 py-2 font-mono text-[12.5px] leading-5 text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/60`}>{approval.detail}</pre> : null}
    {size || approval.truncated ? <div className="-mt-1 flex min-h-6 flex-wrap items-center justify-between gap-x-3 gap-y-1 text-xs leading-5 text-muted-foreground" data-approval-detail-meta>
      <span>{size}{approval.truncated ? <span className="font-medium text-foreground">{size ? " " : ""}(truncated)</span> : null}{approval.truncated ? <span className="sr-only"> The server shortened this text: only the beginning is shown.</span> : null}</span>
      {size ? <button type="button" aria-expanded={expanded} aria-controls={detailId} data-scroll-anchor-toggle className="rounded px-1 font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/60 [@media(pointer:coarse)]:min-h-11" onClick={() => setExpanded((value) => !value)}>{expanded ? "Collapse" : "Expand"}</button> : null}
    </div> : null}
    <div className="flex items-center justify-end gap-2">
      <Button type="button" variant="ghost" size="sm" className="min-h-10 px-3 sm:min-h-8" disabled={sent !== null} onClick={() => void answer("deny")}>{sent === "deny" ? "Declining…" : "Decline"}</Button>
      <Button type="button" size="sm" className="min-h-10 px-3 sm:min-h-8" disabled={sent !== null} onClick={() => void answer("approve")}>{sent === "approve" ? "Approving…" : "Approve"}</Button>
    </div>
    {error ? <p role="alert" className="text-xs leading-5 text-destructive">{error}</p> : null}
  </div>;
}
