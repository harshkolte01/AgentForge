"use client";
import { useState } from "react";
import { CircleCheck, CircleX, Clock, ExternalLink, Minus } from "lucide-react";
import { linkClass } from "@/features/delivery/styles";
import type { CheckItem, PullRequest } from "@/lib/delivery-api";
import { checksSummary, plural, safeHref, sortChecks, type Tone } from "@/lib/delivery-status";

/** Beyond this many checks, the ones that passed fold away behind a toggle (failing and pending ones always show). */
const FOLD_AFTER = 6;

const marks: Record<CheckItem["status"], { Icon: typeof CircleX; className: string; label: string }> = {
  failed: { Icon: CircleX, className: "text-red-600 dark:text-red-400", label: "Failed" },
  pending: { Icon: Clock, className: "text-amber-600 dark:text-amber-400", label: "Pending" },
  passed: { Icon: CircleCheck, className: "text-emerald-600 dark:text-emerald-400", label: "Passed" },
  neutral: { Icon: Minus, className: "text-muted-foreground", label: "Neutral" },
};

export const checksUnavailable = (pr: PullRequest, checksReadable: boolean | null) => pr.checks.state === "unavailable" || checksReadable === false;

/** The one-line answer for the Checks row: what is happening, and whether it deserves attention. */
export function checksValue(pr: PullRequest, checksReadable: boolean | null): { text: string; tone: Tone } {
  if (checksUnavailable(pr, checksReadable)) return { text: "Not available", tone: "muted" };
  const { checks } = pr;
  if (checks.state === "none") return { text: "None", tone: "muted" };
  const text = checksSummary(checks) || "Starting…";
  return { text, tone: checks.state === "failing" ? "red" : checks.state === "pending" ? "yellow" : "muted" };
}

/** The pull request's checks, worst first. Each row links to the run on GitHub. */
export function ChecksList({ pr, checksReadable }: { pr: PullRequest; checksReadable: boolean | null }) {
  const [all, setAll] = useState(false);
  const { checks } = pr;
  const sorted = sortChecks(checks.items);
  const folded = !all && sorted.length > FOLD_AFTER;
  const shown = folded ? sorted.filter((item) => item.status === "failed" || item.status === "pending") : sorted;
  const hidden = sorted.length - shown.length;
  if (checksUnavailable(pr, checksReadable)) return <p className="text-xs leading-5 text-muted-foreground">The GitHub App needs the Checks: Read permission to show these.</p>;
  if (!sorted.length) return <p className="text-xs leading-5 text-muted-foreground">{checks.state === "none" ? "No checks have run on this branch." : "Checks are starting…"}</p>;
  return <div>
    <ul className="space-y-px">
      {shown.map((item, index) => {
        const { Icon, className, label } = marks[item.status];
        const href = safeHref(item.url);
        return <li key={`${item.workflow ?? ""}/${item.name}/${index}`} className="flex min-h-8 items-center gap-2 py-1 text-[13px] pointer-coarse:min-h-11">
          <Icon className={`size-4 shrink-0 ${className}`} aria-hidden="true" /><span className="sr-only">{label}: </span>
          <span className="min-w-0 flex-1 truncate" title={item.workflow ? `${item.workflow} / ${item.name}` : item.name}>{item.workflow ? <span className="text-muted-foreground">{item.workflow} / </span> : null}{item.name}</span>
          {href ? <a href={href} target="_blank" rel="noreferrer noopener" className={`inline-flex shrink-0 items-center text-muted-foreground ${linkClass} pointer-coarse:min-h-11`} aria-label={`Details for ${item.name}`}><ExternalLink className="size-3.5" aria-hidden="true" /></a> : null}
        </li>;
      })}
    </ul>
    {sorted.length > FOLD_AFTER ? <button type="button" aria-expanded={all} onClick={() => setAll((value) => !value)} className="mt-1 inline-flex min-h-6 items-center rounded text-xs text-muted-foreground underline underline-offset-4 outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60 pointer-coarse:min-h-11">{all ? "Show only failing and pending" : `Show ${plural(hidden, "more check")}`}</button> : null}
  </div>;
}
