"use client";
import { useState } from "react";
import { ShieldQuestion } from "lucide-react";
import { Button } from "@/components/ui/button";
import { approvalTitle, type PendingApproval } from "@/lib/activity/approvals";
import { answerApproval } from "@/lib/approvals-api";
import { message } from "@/lib/task-api";

/**
 * The agent stopped before something that changes something and is waiting for an answer. The card goes away when the run's
 * `approval.decided` event arrives, so it never claims an answer the server has not recorded.
 */
export function ApprovalCard({ approval }: { approval: PendingApproval }) {
  const [sent, setSent] = useState<"approve" | "deny" | null>(null);
  const [error, setError] = useState("");
  async function answer(decision: "approve" | "deny") {
    setSent(decision); setError("");
    try { await answerApproval(approval.id, decision); }
    catch (failure) { setSent(null); setError(message(failure)); }
  }
  const title = approvalTitle(approval.tool);
  return <div role="group" aria-label={title} data-approval-card className="message-in flex min-w-0 flex-col gap-2.5 rounded-2xl border bg-background p-3.5 shadow-[0_1px_2px_rgb(0_0_0/0.03)]">
    <p className="flex items-center gap-2 text-[13px] font-medium leading-5 text-foreground"><ShieldQuestion className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />{title}</p>
    {approval.detail ? <pre className="max-h-44 min-w-0 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted px-3 py-2 font-mono text-[12.5px] leading-5 text-foreground">{approval.detail}</pre> : null}
    <div className="flex items-center justify-end gap-2">
      <Button type="button" variant="ghost" size="sm" className="min-h-10 px-3 sm:min-h-8" disabled={sent !== null} onClick={() => void answer("deny")}>{sent === "deny" ? "Declining…" : "Decline"}</Button>
      <Button type="button" size="sm" className="min-h-10 px-3 sm:min-h-8" disabled={sent !== null} onClick={() => void answer("approve")}>{sent === "approve" ? "Approving…" : "Approve"}</Button>
    </div>
    {error ? <p role="alert" className="text-xs leading-5 text-destructive">{error}</p> : null}
  </div>;
}
