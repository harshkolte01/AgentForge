"use client";
import { useId, useMemo, useState } from "react";
import { ChevronRight, FileMinus, FilePen, FilePlus, RefreshCw, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDeliveryContext } from "@/features/delivery/delivery-context";
import { DiffView } from "@/features/delivery/diff-view";
import { linkClass, smallButton } from "@/features/delivery/styles";
import type { ChangedFile } from "@/lib/delivery-api";
import { blockedLabels, deliveryNotices, gateAction, hashText, isExplained, plural, splitPath } from "@/lib/delivery-status";
import { isReviewed, loadReviewed, saveReviewed, type ReviewedMarks } from "@/lib/delivery-reviewed";

const statusIcon = {
  added: { Icon: FilePlus, className: "text-emerald-600 dark:text-emerald-400", label: "Added" },
  modified: { Icon: FilePen, className: "text-amber-600 dark:text-amber-400", label: "Modified" },
  deleted: { Icon: FileMinus, className: "text-red-600 dark:text-red-400", label: "Deleted" },
} as const;

function Counts({ additions, deletions, binary }: { additions: number; deletions: number; binary?: boolean }) {
  if (binary) return <span className="shrink-0 text-xs text-muted-foreground">binary</span>;
  return <span className="shrink-0 font-mono text-xs tabular-nums"><span className="text-emerald-700 dark:text-emerald-400">+{additions}</span> <span className="text-red-700 dark:text-red-400">−{deletions}</span></span>;
}

function FileRow({ file, hash, reviewed, expanded, canDiscard, discardReason, confirming, busy, onToggle, onReview, onAskDiscard, onDiscard, onCancelDiscard }: {
  file: ChangedFile; hash: string; reviewed: boolean; expanded: boolean; canDiscard: boolean; discardReason: string | null; confirming: boolean; busy: boolean;
  onToggle: () => void; onReview: (reviewed: boolean) => void; onAskDiscard: () => void; onDiscard: () => void; onCancelDiscard: () => void;
}) {
  const id = useId();
  const { dir, name } = splitPath(file.path);
  const { Icon, className, label } = statusIcon[file.status];
  return <li data-file-path={file.path} data-file-hash={hash} className="min-w-0">
    <div className="flex items-center gap-0.5 px-1.5">
      <button type="button" aria-expanded={expanded} aria-controls={`${id}-diff`} onClick={onToggle} className="flex min-h-9 min-w-0 flex-1 items-center gap-2 rounded-lg px-1.5 text-left text-[13px] outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/60 motion-reduce:transition-none pointer-coarse:min-h-11">
        <ChevronRight className={`size-3.5 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none ${expanded ? "rotate-90" : ""}`} aria-hidden="true" />
        <Icon className={`size-3.5 shrink-0 ${className}`} aria-hidden="true" /><span className="sr-only">{label}: </span>
        <span className={`min-w-0 flex-1 truncate ${reviewed ? "text-muted-foreground" : ""}`} title={file.path}><span className="text-muted-foreground">{dir}</span>{name}</span>
        {file.blocked ? <span title={blockedLabels[file.blocked]} className="shrink-0 rounded-full bg-destructive/10 px-1.5 text-[11px] font-medium leading-5 text-destructive"><span aria-hidden="true">Can&apos;t publish</span><span className="sr-only">Cannot be published: {blockedLabels[file.blocked].toLowerCase()}</span></span> : null}
        <Counts additions={file.additions} deletions={file.deletions} binary={file.binary} />
      </button>
      <label className="inline-flex min-h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-lg px-1.5 text-xs text-muted-foreground hover:bg-muted pointer-coarse:min-h-11">
        <input type="checkbox" checked={reviewed} onChange={(event) => onReview(event.target.checked)} aria-label={`Mark ${file.path} as reviewed`} className="size-3.5 accent-foreground pointer-coarse:size-4" />
        <span aria-hidden="true" className="max-sm:hidden">Reviewed</span>
      </label>
      <Button type="button" variant="ghost" size="icon-sm" className="size-8 text-muted-foreground pointer-coarse:size-11" aria-label={`Discard changes to ${file.path}`} title={canDiscard ? "Discard this file's changes" : discardReason ?? undefined} disabled={!canDiscard || busy} onClick={onAskDiscard}><Undo2 className="size-3.5" /></Button>
    </div>
    {confirming ? <div role="alertdialog" aria-label={`Discard changes to ${file.path}`} className="mx-2 mb-1.5 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs leading-5">
      <span className="min-w-0 flex-1">Discard your changes to <strong className="break-all font-medium">{file.path}</strong>? This can&apos;t be undone.</span>
      <span className="flex gap-1.5"><Button type="button" variant="ghost" size="sm" className={smallButton} onClick={onCancelDiscard} autoFocus>Keep</Button><Button type="button" variant="destructive" size="sm" className={smallButton} disabled={busy} onClick={onDiscard}>{busy ? "Discarding…" : "Discard"}</Button></span>
    </div> : null}
    {expanded ? <div id={`${id}-diff`} className="mx-2 mb-2 overflow-hidden rounded-lg border bg-background"><DiffView file={file} /></div> : null}
  </li>;
}

/** The Changes tab: what the sandbox holds that is not published yet, file by file, with each file's diff on demand. */
export function ChangesView() {
  const delivery = useDeliveryContext();
  const [marks, setMarks] = useState<ReviewedMarks>(() => (delivery ? loadReviewed(delivery.sessionId) : {}));
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const [discarding, setDiscarding] = useState<string | null>(null);
  const files = delivery?.data.changes?.files;
  const hashes = useMemo(() => new Map((files ?? []).map((file) => [file.path, hashText(`${file.status}\n${file.patch}`)])), [files]);
  if (!delivery) return null;
  const { data, sessionId } = delivery;
  const { changes, view } = data;
  if (!view) return null;

  if (!changes) {
    return data.changesStatus === "error"
      ? <div role="alert" className="flex flex-wrap items-center gap-2 px-4 py-4 text-sm text-muted-foreground"><span className="min-w-0 flex-1">Couldn&apos;t read the changes. {data.changesError}</span><Button variant="outline" size="sm" className={smallButton} onClick={data.refreshChanges}><RefreshCw />Retry</Button></div>
      : <div role="status" className="space-y-2.5 px-4 py-4"><span className="sr-only">Loading changes…</span>{[0, 1, 2].map((index) => <div key={index} className="h-8 animate-pulse rounded-lg bg-muted motion-reduce:animate-none" />)}</div>;
  }

  const discardGate = gateAction("discard", view, changes);
  const reviewedCount = changes.files.filter((file) => isReviewed(marks, file.path, hashes.get(file.path) ?? "")).length;
  const setReviewed = (file: ChangedFile, on: boolean) => {
    const hash = hashes.get(file.path) ?? "";
    const next = { ...marks };
    if (on) next[file.path] = hash; else delete next[file.path];
    setMarks(saveReviewed(sessionId, next, hashes));
    // Marking a file reviewed folds it away; unmarking opens it again.
    setOpened((current) => ({ ...current, [file.path]: !on }));
  };
  const isExpanded = (path: string) => opened[path] ?? changes.files.length === 1;

  return <div className="flex flex-col">
    <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-4 py-2 text-xs text-muted-foreground">
      <p role="status">{changes.files.length ? <><span className="font-medium text-foreground">{plural(changes.files.length, "file")}</span> · <span className="text-emerald-700 dark:text-emerald-400">+{changes.additions}</span> <span className="text-red-700 dark:text-red-400">−{changes.deletions}</span>{changes.files.length > 1 ? ` · ${reviewedCount} reviewed` : ""}</> : "No unpublished changes"}</p>
      <p>{changes.baseline === "published" ? "Compared with the last push" : "Compared with the original code"}</p>
    </div>
    {changes.files.length ? <ul className="flex flex-col gap-0.5 pb-2">
      {changes.files.map((file) => <FileRow key={file.path} file={file} hash={hashes.get(file.path) ?? ""} reviewed={isReviewed(marks, file.path, hashes.get(file.path) ?? "")} expanded={isExpanded(file.path)}
        canDiscard={discardGate.enabled} discardReason={discardGate.reason} confirming={discarding === file.path} busy={data.busyAction === "discard"}
        onToggle={() => setOpened((current) => ({ ...current, [file.path]: !isExpanded(file.path) }))} onReview={(on) => setReviewed(file, on)}
        onAskDiscard={() => setDiscarding(file.path)} onCancelDiscard={() => setDiscarding(null)}
        onDiscard={() => { void data.discard(file.path).then((ok) => { if (ok) setDiscarding(null); }); }} />)}
    </ul> : <p className="px-4 pb-4 pt-1 text-sm leading-6 text-muted-foreground">{view.state === "none" ? "Nothing has changed in the workspace yet. When the agent edits files, they appear here for review." : "Everything the agent changed has been published."}</p>}
    {changes.truncated ? <p className="border-t px-4 py-2.5 text-xs leading-5 text-muted-foreground">More files changed than are shown here. Publish what you can see, then review the rest on GitHub{view.branch_url ? <> (<a href={view.branch_url} target="_blank" rel="noreferrer noopener" className={linkClass}>open the branch</a>)</> : null}.</p> : null}
    {changes.files.length && !discardGate.enabled && discardGate.reason && !isExplained(deliveryNotices(view, changes), discardGate.reason) ? <p className="border-t px-4 py-2.5 text-xs leading-5 text-muted-foreground">Discarding is unavailable: {discardGate.reason.replace(/…$/, "")}.</p> : null}
  </div>;
}
