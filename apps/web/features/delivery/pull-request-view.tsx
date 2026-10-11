"use client";
import { useId, useState, type ReactNode } from "react";
import { ChevronRight, ExternalLink, GitBranch, GitPullRequest, GitPullRequestClosed, GitPullRequestDraft, GitMerge, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChecksList, checksValue } from "@/features/delivery/checks-list";
import { useDeliveryContext } from "@/features/delivery/delivery-context";
import { linkClass, smallButton, toneText } from "@/features/delivery/styles";
import { closePullRequest, type PullRequest, type ReviewState } from "@/lib/delivery-api";
import { firstLine, gateAction, isOpen, plural, relativeTime, safeHref, shortSha, type Tone } from "@/lib/delivery-status";

const stateStyle: Record<PullRequest["state"], { label: string; Icon: typeof GitPullRequest; tone: Tone }> = {
  open: { label: "Open", Icon: GitPullRequest, tone: "green" },
  draft: { label: "Draft", Icon: GitPullRequestDraft, tone: "muted" },
  merged: { label: "Merged", Icon: GitMerge, tone: "purple" },
  closed: { label: "Closed", Icon: GitPullRequestClosed, tone: "red" },
};
const reviewLabel: Record<ReviewState, string> = { approved: "approved", changes_requested: "requested changes", commented: "commented", dismissed: "dismissed", pending: "pending" };

function External({ href, children, label }: { href: string | undefined; children: ReactNode; label?: string }) {
  return href ? <a href={href} target="_blank" rel="noreferrer noopener" aria-label={label} className={`inline-flex items-center gap-1 ${linkClass}`}>{children}<ExternalLink className="size-3 shrink-0" aria-hidden="true" /></a> : <span>{children}</span>;
}

/** One quiet line (a name and its answer) that opens to the detail. `auto` opens it by itself while it needs attention, until you choose. */
function Row({ title, value, tone = "muted", auto = false, children }: { title: string; value: string; tone?: Tone; auto?: boolean; children: ReactNode }) {
  const id = useId();
  const [choice, setChoice] = useState<boolean | null>(null);
  const open = choice ?? auto;
  return <div>
    <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setChoice(!open)}
      className="flex min-h-11 w-full items-center gap-2 rounded text-left text-[13px] outline-none focus-visible:ring-2 focus-visible:ring-ring/60 sm:min-h-10">
      <span className="font-medium">{title}</span>
      <span className={`ml-auto min-w-0 truncate text-xs ${toneText[tone]}`}>{value}</span>
      <ChevronRight className={`size-3.5 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""}`} aria-hidden="true" />
    </button>
    {open ? <div id={id} className="pb-3">{children}</div> : null}
  </div>;
}

function reviewsValue(pr: PullRequest): { text: string; tone: Tone } {
  const { decision, items } = pr.reviews;
  if (decision === "approved") return { text: "Approved", tone: "green" };
  if (decision === "changes_requested") return { text: "Changes requested", tone: "red" };
  if (decision === "review_required") return { text: "Review required", tone: "yellow" };
  return { text: items.length ? `${items.length} commented` : "None yet", tone: "muted" };
}

/** The Pull request tab: what it is, then checks, reviews and commits as one-line rows, and closing it. */
export function PullRequestView() {
  const delivery = useDeliveryContext();
  const [confirmClose, setConfirmClose] = useState(false);
  const view = delivery?.data.view;
  if (!delivery || !view) return null;
  const { data, summary } = delivery;
  const pr = view.pull_request;

  if (!pr) {
    const unpublished = summary?.files ?? 0;
    const failed = !!(view.sync_error || data.readFailed);
    return <div className="flex flex-col items-start gap-3 px-4 py-5 text-sm leading-6 text-muted-foreground">
      {failed ? <>
        <p>We couldn&apos;t read this pull request&apos;s status from GitHub, so it isn&apos;t known whether one exists.</p>
        <Button variant="outline" size="sm" className={smallButton} disabled={data.refreshing} onClick={data.refresh}><RefreshCw />{data.refreshing ? "Checking…" : "Retry"}</Button>
      </> : <>
        <p>{view.state === "branch" ? <>The branch <strong className="font-medium text-foreground">{view.branch}</strong> is published, but no pull request is open for it.</> : unpublished ? `No pull request yet. Create one to publish ${plural(unpublished, "changed file")}.` : "No pull request yet. When the agent has made changes, you can open one from here."}</p>
        {unpublished > 0 || view.state === "branch" ? <Button size="sm" className={smallButton} onClick={() => delivery.openPublish()} disabled={!gateAction("create_pull_request", view, data.changes).enabled}>Create pull request</Button> : null}
      </>}
    </div>;
  }

  const { label, Icon, tone } = stateStyle[pr.state];
  const gate = gateAction("close", view, data.changes);
  const branchHref = safeHref(view.branch_url);
  const checks = checksValue(pr, view.capabilities.checks_readable);
  const reviews = reviewsValue(pr);
  return <div className="flex flex-col gap-4 px-4 py-4">
    <div className="min-w-0 space-y-1.5">
      <div className="flex items-start gap-2">
        <Icon className={`mt-1 size-4 shrink-0 ${toneText[tone]}`} aria-hidden="true" />
        <h3 className="min-w-0 flex-1 break-words text-[15px] font-medium leading-6">{pr.title}</h3>
      </div>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 pl-6 text-xs text-muted-foreground">
        <span className={`font-medium ${toneText[tone]}`}>{label}</span>
        <External href={safeHref(pr.url)} label={`Pull request #${pr.number} on GitHub`}>#{pr.number}</External>
        {pr.merged_at ? <span>merged {relativeTime(pr.merged_at)}</span> : null}
      </p>
      <p className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 pl-6 text-xs text-muted-foreground">
        <GitBranch className="size-3.5 shrink-0" aria-hidden="true" />
        <External href={branchHref}><code className="break-all font-mono text-foreground">{view.branch ?? "branch"}</code></External>
        <span aria-hidden="true">→</span><span className="sr-only">into</span><code className="font-mono text-foreground">{view.base_branch ?? "base"}</code>
        {pr.behind_base ? <span className={toneText.yellow}>· {plural(pr.behind_base, "commit")} behind</span> : null}
        {pr.mergeable === "conflicting" ? <span className={toneText.red}>· has conflicts</span> : null}
      </p>
    </div>

    <div className="divide-y border-y">
      <Row title="Checks" value={checks.text} tone={checks.tone} auto={pr.checks.state === "failing" || pr.checks.state === "pending"}>
        <ChecksList pr={pr} checksReadable={view.capabilities.checks_readable} />
      </Row>
      <Row title="Reviews" value={reviews.text} tone={reviews.tone} auto={pr.reviews.decision === "changes_requested"}>
        {pr.reviews.items.length ? <ul className="space-y-px">
          {pr.reviews.items.map((review, index) => <li key={`${review.login}-${index}`} className="flex min-h-8 items-center justify-between gap-2 py-1 text-[13px]"><span className="min-w-0 truncate">{review.login}</span><span className="shrink-0 text-xs text-muted-foreground">{reviewLabel[review.state]}</span></li>)}
        </ul> : <p className="text-xs leading-5 text-muted-foreground">Nobody has reviewed this yet.</p>}
      </Row>
      <Row title="Commits" value={view.commits.length ? plural(view.commits.length, "commit") : "None"}>
        {view.commits.length ? <ul className="space-y-2">
          {view.commits.map((commit) => <li key={commit.sha} className="flex items-start gap-2 text-[13px]">
            <span className="min-w-0 flex-1"><span className="line-clamp-2 break-words">{firstLine(commit.message)}</span><span className="mt-0.5 block text-xs text-muted-foreground">{plural(commit.files, "file")} <span className="text-emerald-700 dark:text-emerald-400">+{commit.additions}</span> <span className="text-red-700 dark:text-red-400">−{commit.deletions}</span> · {relativeTime(commit.created_at)}</span></span>
            <External href={safeHref(commit.url)} label={`Commit ${shortSha(commit.sha)} on GitHub`}><code className="font-mono text-xs">{shortSha(commit.sha)}</code></External>
          </li>)}
        </ul> : <p className="text-xs leading-5 text-muted-foreground">AgentForge hasn&apos;t pushed any commits to this branch.</p>}
      </Row>
    </div>

    {isOpen(pr) ? (confirmClose
      ? <div role="alertdialog" aria-label={`Close pull request #${pr.number}`} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs leading-5">
        <span className="min-w-0 flex-1">Close #{pr.number} without merging? It stays on GitHub, and you can start again from here.</span>
        <span className="flex gap-1.5"><Button type="button" variant="ghost" size="sm" className={smallButton} autoFocus onClick={() => setConfirmClose(false)}>Keep open</Button><Button type="button" variant="destructive" size="sm" className={smallButton} disabled={data.busyAction === "close"} onClick={() => { void data.run("close", "close", (key) => closePullRequest(delivery.sessionId, key)).then((outcome) => { if (outcome.ok) setConfirmClose(false); }); }}>{data.busyAction === "close" ? "Closing…" : "Close pull request"}</Button></span>
      </div>
      : <div className="-ml-2 flex flex-wrap items-center gap-2">
        <Button type="button" variant="ghost" size="sm" className={`text-muted-foreground ${smallButton}`} disabled={!gate.enabled} title={gate.reason ?? undefined} onClick={() => setConfirmClose(true)}>Close pull request</Button>
        {!gate.enabled && gate.reason ? <span className="text-xs text-muted-foreground">{gate.reason}</span> : null}
      </div>) : null}
  </div>;
}
