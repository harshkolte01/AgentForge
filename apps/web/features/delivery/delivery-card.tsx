"use client";
import { FileDiff, GitBranch, GitMerge, GitPullRequest, GitPullRequestClosed, GitPullRequestDraft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDeliveryContext } from "@/features/delivery/delivery-context";
import { smallButton, toneDot } from "@/features/delivery/styles";
import { actionLabels, chipLabel, gateAction, plural } from "@/lib/delivery-status";

/**
 * Under the latest answer: what that work amounts to and the next step, as one compact line ("3 files · +40 −5 · Review changes ·
 * Create pull request"). It becomes the pull request's status once there is one. Everything else is in the panel.
 */
export function DeliveryCard() {
  const delivery = useDeliveryContext();
  if (!delivery?.cardVisible || !delivery.headline || !delivery.data.view) return null;
  const { data, summary, headline, working, panelOpen } = delivery;
  const view = data.view!;
  const pr = view.pull_request;
  const files = summary?.files ?? 0;
  const action = headline.action;
  const gate = action ? gateAction(action, view, data.changes) : null;
  const Icon = pr ? (pr.state === "merged" ? GitMerge : pr.state === "closed" ? GitPullRequestClosed : pr.state === "draft" ? GitPullRequestDraft : GitPullRequest) : files > 0 ? FileDiff : GitBranch;
  const { label } = chipLabel(view, summary);
  return <div className="mt-3 flex flex-col gap-2" data-delivery-card>
    <div role="group" aria-label={pr ? "Pull request" : "Changes ready to publish"} className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5 rounded-xl border bg-background px-3 py-2 text-[13px] leading-5 dark:bg-card">
      <span className="flex min-w-0 items-center gap-2">
        <span aria-hidden="true" className={`size-1.5 shrink-0 rounded-full ${toneDot[headline.tone]}`} />
        <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        {pr ? <span className="min-w-0 truncate font-medium">{label}</span>
          : files > 0 ? <span className="min-w-0 truncate"><span className="font-medium">{plural(files, "file")}</span> <span className="font-mono text-xs tabular-nums"><span className="text-emerald-700 dark:text-emerald-400">+{summary?.additions ?? 0}</span> <span className="text-red-700 dark:text-red-400">−{summary?.deletions ?? 0}</span></span></span>
            : <span className="min-w-0 truncate font-medium">{headline.label}</span>}
      </span>
      <span className="ml-auto flex flex-wrap items-center gap-1.5">
        <Button type="button" variant="ghost" size="sm" className={`h-7 ${smallButton}`} aria-expanded={panelOpen} onClick={() => delivery.openPanel(files > 0 || !pr ? "changes" : "pull_request")}>{files > 0 ? "Review changes" : pr ? "View pull request" : "Open"}</Button>
        {action ? <Button type="button" size="sm" className={`h-7 ${smallButton}`} disabled={!gate?.enabled || working} title={gate?.reason ?? undefined} onClick={() => delivery.perform(action)}>{action === "merge" ? "Review and merge" : actionLabels[action]}</Button> : null}
      </span>
    </div>
    {/* The panel shows a failed action itself; with it closed the card is the only place to say so. */}
    {data.failure && !panelOpen ? <p role="alert" className="flex flex-wrap items-center gap-x-2 text-xs leading-5 text-destructive">{data.failure.message}<button type="button" onClick={data.dismissFailure} className="rounded font-medium underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/60 pointer-coarse:min-h-11">Dismiss</button></p> : null}
  </div>;
}
