"use client";
import Link from "next/link";
import { useEffect, useId, useRef, type CSSProperties, type KeyboardEvent } from "react";
import { GitPullRequest, Info, RefreshCw, TriangleAlert, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ChangesView } from "@/features/delivery/changes-view";
import { useDeliveryContext, type PanelTab } from "@/features/delivery/delivery-context";
import { PullRequestView } from "@/features/delivery/pull-request-view";
import { minWidth, usePanelWidth } from "@/features/delivery/use-panel-width";
import { StatusStrip } from "@/features/delivery/status-strip";
import { linkClass, smallButton } from "@/features/delivery/styles";
import { deliveryNotices } from "@/lib/delivery-status";
import { routes } from "@/lib/routes";

const tabs: { id: PanelTab; label: string }[] = [{ id: "changes", label: "Changes" }, { id: "pull_request", label: "Pull request" }];

function Skeleton() {
  return <div role="status" className="space-y-3 px-4 py-4"><span className="sr-only">Loading delivery status…</span>
    <div className="h-5 w-40 animate-pulse rounded bg-muted motion-reduce:animate-none" /><div className="h-9 w-full animate-pulse rounded-lg bg-muted motion-reduce:animate-none" />
    <div className="space-y-2 pt-3">{[0, 1, 2].map((index) => <div key={index} className="h-8 animate-pulse rounded-lg bg-muted motion-reduce:animate-none" />)}</div></div>;
}

/** Why delivery cannot be used here, and the one step that fixes it when there is one. */
function Unavailable({ reason }: { reason: "sandbox_not_configured" | "github_not_connected" | "no_repository" | null }) {
  return <div className="space-y-2 px-4 py-5 text-sm leading-6" data-unavailable={reason ?? undefined}>
    {reason === "github_not_connected" ? <>
      <p className="font-medium">Connect GitHub to publish</p>
      <p className="text-muted-foreground">Publishing creates a branch and a pull request with your GitHub account, so GitHub has to be connected first.</p>
      <Link href={routes.settings("github")} className="inline-flex min-h-10 items-center rounded-lg border bg-background px-3 text-sm font-medium outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/60 sm:min-h-8">Open GitHub settings</Link>
    </> : reason === "sandbox_not_configured" ? <>
      <p className="font-medium">No workspace to publish from</p>
      <p className="text-muted-foreground">This server has no coding sandbox set up, so the agent has no workspace whose changes could be reviewed or published. The operator needs to configure it.</p>
    </> : <p className="text-muted-foreground">Delivery isn&apos;t available for this conversation.</p>}
  </div>;
}

function Body() {
  const delivery = useDeliveryContext();
  const id = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const resize = usePanelWidth();
  useEffect(() => {
    // Opening the panel moves you into it; closing it puts you back where you were.
    const previous = document.activeElement as HTMLElement | null;
    heading.current?.focus({ preventScroll: true });
    return () => { if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, []);
  if (!delivery) return null;
  const { data, tab, setTab, closePanel, sheet, summary } = delivery;
  const { view } = data;
  // "Confirming with GitHub…" is shown by the status strip itself, beside the action it replaces.
  // (Missing Checks permission is said once, in the Checks row of the Pull request tab.)
  const notices = view?.available ? deliveryNotices(view, data.changes).filter((notice) => notice.id !== "pending" && notice.id !== "checks") : [];
  const index = tabs.findIndex((item) => item.id === tab);
  const move = (event: KeyboardEvent<HTMLButtonElement>) => {
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step && event.key !== "Home" && event.key !== "End") return;
    event.preventDefault();
    const next = tabs[event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + step + tabs.length) % tabs.length];
    setTab(next.id);
    document.getElementById(`${id}-tab-${next.id}`)?.focus();
  };

  return <>
    {sheet ? <button type="button" tabIndex={-1} aria-label="Close delivery panel" className="fixed inset-0 z-30 bg-foreground/20 lg:hidden" onClick={closePanel} /> : null}
    <aside id="delivery-panel" data-delivery-panel role={sheet ? "dialog" : "complementary"} aria-modal={sheet || undefined} aria-label="Delivery"
      onKeyDown={(event) => { if (event.key === "Escape" && !event.defaultPrevented) { event.stopPropagation(); closePanel(); } }}
      style={{ "--delivery-width": `${resize.width}px` } as CSSProperties}
      className="flex min-h-0 flex-col bg-background max-lg:fixed max-lg:inset-x-0 max-lg:bottom-0 max-lg:top-12 max-lg:z-40 max-lg:rounded-t-2xl max-lg:border-t max-lg:shadow-[0_-12px_32px_-12px_rgb(0_0_0/0.25)] max-lg:motion-safe:animate-in max-lg:motion-safe:slide-in-from-bottom-6 lg:relative lg:w-[var(--delivery-width)] lg:shrink-0 lg:border-l">
      {/* The drag handle: the panel's left edge. Dragging left widens it; arrows, Home, End and Enter work too. */}
      <div role="separator" aria-label="Resize delivery panel" aria-orientation="vertical" aria-controls="delivery-panel" aria-valuemin={minWidth} aria-valuemax={resize.max} aria-valuenow={resize.width}
        aria-valuetext={`${resize.width} pixels wide`} aria-describedby={`${id}-resize-help`} tabIndex={0} {...resize.separator}
        className={`absolute inset-y-0 -left-1 z-20 hidden w-2 touch-none cursor-col-resize outline-none before:absolute before:inset-y-0 before:left-[3px] before:w-0.5 before:rounded-full before:transition-colors hover:before:bg-foreground/25 focus-visible:before:bg-foreground/50 motion-reduce:before:transition-none lg:block ${resize.dragging ? "before:bg-foreground/40" : ""}`} />
      <span id={`${id}-resize-help`} className="sr-only">Use the left and right arrow keys to resize. Home and End set the narrowest and widest. Enter or double-click resets the width.</span>
      <header className="flex min-h-12 shrink-0 items-center gap-2 border-b pl-4 pr-2">
        <GitPullRequest className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <h2 ref={heading} tabIndex={-1} className="min-w-0 flex-1 truncate text-[13px] font-normal text-muted-foreground outline-none"><span className="sr-only">Delivery: </span>{view?.repo_id ?? "Delivery"}</h2>
        <Button type="button" variant="ghost" size="icon-sm" className="size-8 pointer-coarse:size-11" aria-label="Refresh from GitHub" disabled={data.refreshing || !view} onClick={data.refresh}><RefreshCw className={data.refreshing ? "animate-spin motion-reduce:animate-none" : ""} /></Button>
        <Button type="button" variant="ghost" size="icon-sm" className="size-8 pointer-coarse:size-11" aria-label="Close delivery panel" onClick={closePanel}><X /></Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
        {!view ? (data.readFailed
          ? <div role="alert" className="flex flex-col items-start gap-3 px-4 py-5 text-sm leading-6"><p className="flex items-start gap-2"><TriangleAlert className="mt-1 size-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden="true" />Delivery status unavailable. {data.readFailed}</p><Button variant="outline" size="sm" className={smallButton} disabled={data.refreshing} onClick={data.refresh}><RefreshCw />Retry</Button></div>
          : <Skeleton />)
          : !view.available ? <Unavailable reason={view.unavailable_reason} />
            : <>
              <StatusStrip />
              {notices.length ? <ul className="space-y-1.5 border-b px-4 py-2.5" aria-label="Notes">{notices.map((notice) => <li key={notice.id} className={`flex items-start gap-2 text-xs leading-5 ${notice.tone === "warn" ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground"}`}>
                {notice.tone === "warn" ? <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" /> : <Info className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />}
                <span>{notice.text}{notice.link ? <> <a href={notice.link.href} className={linkClass}>{notice.link.label}</a></> : null}</span></li>)}</ul> : null}
              <div role="tablist" aria-label="Delivery sections" className="sticky top-0 z-10 flex gap-1 border-b bg-background px-3">
                {tabs.map((item) => <button key={item.id} id={`${id}-tab-${item.id}`} type="button" role="tab" aria-selected={tab === item.id} aria-controls={`${id}-panel-${item.id}`} tabIndex={tab === item.id ? 0 : -1} onClick={() => setTab(item.id)} onKeyDown={move}
                  className={`relative inline-flex min-h-10 items-center gap-1.5 px-2 text-[13px] font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60 motion-reduce:transition-none pointer-coarse:min-h-11 ${tab === item.id ? "text-foreground after:absolute after:inset-x-1 after:bottom-0 after:h-0.5 after:rounded-full after:bg-foreground" : "text-muted-foreground hover:text-foreground"}`}>
                  {item.label}
                  {item.id === "changes" && summary && summary.files > 0 ? <span className="rounded-full bg-muted px-1.5 text-[11px] font-normal tabular-nums text-muted-foreground">{summary.files}</span> : null}
                </button>)}
              </div>
              <div role="tabpanel" id={`${id}-panel-${tab}`} aria-labelledby={`${id}-tab-${tab}`} tabIndex={-1} className="outline-none">
                {tab === "changes" ? <ChangesView /> : <PullRequestView />}
              </div>
            </>}
      </div>
    </aside>
  </>;
}

/** The panel beside the conversation (large screens) or over it as a sheet (small ones). Nothing is mounted while it is closed. */
export function DeliveryPanel() {
  const delivery = useDeliveryContext();
  return delivery?.panelOpen ? <Body /> : null;
}
