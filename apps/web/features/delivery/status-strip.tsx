"use client";
import { RefreshCw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDeliveryContext } from "@/features/delivery/delivery-context";
import { MergeButtons, MergeNote } from "@/features/delivery/merge-button";
import { linkClass, smallButton, toneDot, toneText } from "@/features/delivery/styles";
import { resetDelivery } from "@/lib/delivery-api";
import { RECONNECT_HREF, needsReconnect, syncErrorMessage } from "@/lib/delivery-errors";
import { actionLabels, agentActions, deliveryNotices, gateAction, isExplained, plural, secondaryActions, type ActionKind } from "@/lib/delivery-status";

const workingLabel: Partial<Record<ActionKind, string>> = {
  ready_for_review: "Marking ready…", update_branch: "Updating…", continue: "Starting over…", fix_checks: "Asking the agent…", resolve: "Asking the agent…", address_feedback: "Asking the agent…",
};

/** The headline and its single primary action, then whatever stands in the way: a failed read, a failed action, a confirmation. */
export function StatusStrip() {
  const delivery = useDeliveryContext();
  const view = delivery?.data.view;
  if (!delivery || !view) return null;
  const { data, headline, summary, working, confirming } = delivery;
  const action = headline?.action ?? null;
  const gate = action ? gateAction(action, view, data.changes) : null;
  const others = headline ? secondaryActions(view, headline) : [];
  // The panel lists standing conditions as notes; a reason they already give is not repeated under the button.
  const explained = isExplained(deliveryNotices(view, data.changes), gate?.reason ?? null);
  const readProblem = view.sync_error ? syncErrorMessage(view.sync_error) : data.readFailed;
  const confirmingContinue = confirming === "continue";

  return <section aria-label="Delivery status" className="space-y-2.5 border-b px-4 py-3.5">
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
      <p role="status" className="flex min-w-0 items-center gap-2 text-[15px] font-medium leading-6" data-headline={headline?.key}>
        <span aria-hidden="true" className={`size-2 shrink-0 rounded-full ${toneDot[headline?.tone ?? "muted"]}`} />
        <span className={`truncate ${headline ? toneText[headline.tone] : ""}`}>{headline?.label ?? "Delivery"}</span>
      </p>
      {view.pending_operation ? <p role="status" className="flex items-center gap-1.5 text-xs text-muted-foreground"><RefreshCw className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />Confirming with GitHub…</p>
        : action === "merge" ? <MergeButtons />
        : action ? <Button type="button" className={`min-h-10 px-3 sm:min-h-8 ${smallButton}`} disabled={!gate?.enabled || working} onClick={() => delivery.perform(action)} aria-describedby={gate && !gate.enabled ? "delivery-gate" : undefined}>{working && workingLabel[action] ? workingLabel[action] : actionLabels[action]}</Button> : null}
    </div>
    {action === "merge" && !view.pending_operation ? <MergeNote /> : null}
    {action !== "merge" && gate && !gate.enabled && gate.reason && !view.pending_operation && !explained ? <p id="delivery-gate" className="text-xs leading-5 text-muted-foreground">{gate.reason}</p> : null}
    {action && agentActions.has(action) && gate?.enabled ? <p className="text-xs leading-5 text-muted-foreground">Asks the agent to fix it in the workspace. You push the result from here.</p> : null}
    {others.length ? <div className="flex flex-wrap gap-1.5">{others.map((other) => {
      const otherGate = gateAction(other, view, data.changes);
      return <Button key={other} type="button" variant="outline" size="sm" className={smallButton} disabled={!otherGate.enabled || working} title={otherGate.reason ?? undefined} onClick={() => delivery.perform(other)}>{actionLabels[other]}</Button>;
    })}</div> : null}

    {confirmingContinue ? <div role="alertdialog" aria-label="Start a fresh branch" className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-xs leading-5">
      <span className="min-w-0 flex-1">Start fresh from the updated {view.base_branch ?? "base branch"}? {summary?.files ? `${plural(summary.files, "file")} in the sandbox that you didn't publish will be discarded.` : "Changes in the sandbox that you didn't publish will be discarded."}</span>
      <span className="flex gap-1.5"><Button type="button" variant="ghost" size="sm" className={smallButton} autoFocus onClick={() => delivery.setConfirming(null)}>Keep working</Button>
        <Button type="button" variant="destructive" size="sm" className={smallButton} disabled={working} onClick={() => { void data.run("continue", "reset:discard", (key) => resetDelivery(delivery.sessionId, key, true),{ clearChanges: true }).then((outcome) => { if (outcome.ok) delivery.setConfirming(null); }); }}>{working ? "Starting over…" : "Start fresh"}</Button></span>
    </div> : null}

    {readProblem ? <div role="status" className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg bg-muted/60 px-3 py-2 text-xs leading-5">
      <TriangleAlert className="size-3.5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden="true" />
      <span className="font-medium">PR status unavailable</span><span aria-hidden="true" className="text-muted-foreground">·</span>
      <button type="button" disabled={data.refreshing} onClick={data.refresh} className={`font-medium ${linkClass} disabled:opacity-60 pointer-coarse:min-h-11`}>{data.refreshing ? "Retrying…" : "Retry"}</button>
      <span className="basis-full text-muted-foreground">{readProblem}{view.sync_error === "github_reauthorization_required" ? <> <a href={RECONNECT_HREF} className={linkClass}>Reconnect GitHub</a></> : null} What&apos;s shown below may be out of date.</span>
    </div> : null}

    {data.failure ? <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs leading-5 text-destructive">
      <span className="min-w-0 flex-1">{data.failure.message}{needsReconnect(data.failure.code) ? <> <a href={RECONNECT_HREF} className="font-medium underline underline-offset-4">Reconnect GitHub</a></> : null}</span>
      <span className="flex gap-1.5">{data.failure.retryable ? <Button type="button" variant="outline" size="sm" className={smallButton} disabled={working} onClick={data.retryFailure}>Retry</Button> : null}
        <Button type="button" variant="ghost" size="sm" className={smallButton} onClick={data.dismissFailure}>Dismiss</Button></span>
    </div> : null}
  </section>;
}
