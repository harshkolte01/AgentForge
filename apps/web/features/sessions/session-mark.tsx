import { GitBranch, GitMerge, GitPullRequest, GitPullRequestClosed, GitPullRequestCreateArrow, GitPullRequestDraft, type LucideIcon } from "lucide-react";
import { SessionStatus } from "@/features/sessions/session-status";
import type { Mark, MarkKey } from "@/lib/conversation-mark";

/** The pull-request glyphs are GitHub's own shapes (lucide's set), so a mark reads the same here as it does on github.com. */
const glyphs: Partial<Record<MarkKey, { Icon: LucideIcon; tone: string }>> = {
  pr_merged: { Icon: GitMerge, tone: "text-violet-600 dark:text-violet-400" },
  pr_closed: { Icon: GitPullRequestClosed, tone: "text-muted-foreground/60" },
  pr_conflicts: { Icon: GitPullRequest, tone: "text-red-600 dark:text-red-400" },
  pr_changes: { Icon: GitPullRequest, tone: "text-red-600 dark:text-red-400" },
  pr_failing: { Icon: GitPullRequest, tone: "text-red-600 dark:text-red-400" },
  pr_running: { Icon: GitPullRequest, tone: "text-amber-600 dark:text-amber-400" },
  pr_draft: { Icon: GitPullRequestDraft, tone: "text-muted-foreground/60" },
  pr_approved: { Icon: GitPullRequest, tone: "text-emerald-600 dark:text-emerald-400" },
  pr_open: { Icon: GitPullRequest, tone: "text-muted-foreground" },
  unpublished: { Icon: GitPullRequestCreateArrow, tone: "text-amber-600 dark:text-amber-400" },
  branch: { Icon: GitBranch, tone: "text-muted-foreground/60" },
};

/** A fixed 16px slot, so every title starts at the same place whatever the mark is (or whether there is one). */
export function SessionMark({ mark }: { mark: Mark }) {
  const slot = "flex size-4 shrink-0 items-center justify-center";
  if (mark.key === "running" || mark.key === "waiting_approval" || mark.key === "failed" || mark.key === "unread") return <span className={slot}><SessionStatus status={mark.key} /></span>;
  const glyph = glyphs[mark.key];
  if (!glyph) return <span className={slot} aria-hidden="true"><span className="size-1 rounded-full bg-muted-foreground/30" /></span>;
  return <span className={slot}><glyph.Icon role="img" aria-label={mark.label} data-mark={mark.key} strokeWidth={1.75} className={`size-4 ${glyph.tone}`} /></span>;
}
