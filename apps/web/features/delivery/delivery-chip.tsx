"use client";
import { FileDiff, GitBranch, GitMerge, GitPullRequest, GitPullRequestClosed, GitPullRequestDraft, TriangleAlert } from "lucide-react";
import { useDeliveryContext } from "@/features/delivery/delivery-context";
import { toneDot } from "@/features/delivery/styles";
import type { DeliveryView } from "@/lib/delivery-api";
import { chipLabel, type ChangeSummary, type Tone } from "@/lib/delivery-status";

function StateIcon({ view, summary }: { view: DeliveryView | null; summary: ChangeSummary }) {
  const state = view?.pull_request?.state;
  const className = "size-3.5 shrink-0 text-muted-foreground";
  if (state === "merged") return <GitMerge className={className} aria-hidden="true" />;
  if (state === "closed") return <GitPullRequestClosed className={className} aria-hidden="true" />;
  if (state === "draft") return <GitPullRequestDraft className={className} aria-hidden="true" />;
  if (state) return <GitPullRequest className={className} aria-hidden="true" />;
  if (!view?.available) return <TriangleAlert className={className} aria-hidden="true" />;
  return summary && summary.files > 0 ? <FileDiff className={className} aria-hidden="true" /> : <GitBranch className={className} aria-hidden="true" />;
}

/** In the conversation header: one glance at where delivery stands, and the way to open the panel. */
export function DeliveryChip() {
  const delivery = useDeliveryContext();
  if (!delivery?.chipVisible) return null;
  const { data, summary, panelOpen } = delivery;
  const { view } = data;
  let label: string, tone: Tone;
  if (!view) { label = "Delivery unavailable"; tone = "muted"; }
  else if (!view.available) { label = view.unavailable_reason === "github_not_connected" ? "Connect GitHub" : "Publishing unavailable"; tone = "muted"; }
  else ({ label, tone } = chipLabel(view, summary));
  return <button type="button" aria-expanded={panelOpen} aria-controls={panelOpen ? "delivery-panel" : undefined} aria-label={`Delivery: ${label}`} title={label} data-delivery-chip
    onClick={() => (panelOpen ? delivery.closePanel() : delivery.openPanel())}
    className="inline-flex min-h-8 min-w-0 max-w-[11rem] items-center gap-1.5 rounded-full border bg-background px-2.5 text-xs font-medium text-foreground outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/60 aria-expanded:bg-muted motion-reduce:transition-none pointer-coarse:min-h-11 sm:max-w-[18rem]">
    <span aria-hidden="true" className={`size-1.5 shrink-0 rounded-full ${toneDot[tone]}`} />
    <StateIcon view={view} summary={summary} />
    <span className="min-w-0 truncate">{label}</span>
  </button>;
}
