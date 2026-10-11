"use client";
import { useEffect, useReducer } from "react";
import { Download } from "lucide-react";
import { CopyButton } from "@/features/sessions/copy-button";
import {
  classifyFailure, createDetailLoader, detailReducer, diffLineKind, diffTotals, errorText, executionRows, initialDetailState, retryableReasons,
  safeDownloadHref, truncationNotice, unavailableMessage,
} from "@/features/sessions/tool-detail-model";
import { formatBytes } from "@/features/sessions/answer-extras-model";
import { fetchToolDetail, type ToolCallDetail, type ToolDetailText } from "@/lib/task-api";

/** One loader for the page: details read once stay available when a row is closed and opened again, or the thread re-renders. */
const loader = createDetailLoader((taskId, toolCallId, init) => fetchToolDetail(taskId, toolCallId, init));

const linkClass = "inline-flex min-h-6 max-w-full items-center gap-2 rounded text-sm font-medium underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/60 [@media(pointer:coarse)]:min-h-11";
const retryClass = "inline-flex min-h-6 items-center rounded px-1 text-sm font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/60 [@media(pointer:coarse)]:min-h-11";

/** Bounded, monospace text with its own scroll area, a copy control and a notice when the server clipped it. */
function LogBlock({ label, text, diff = false }: { label: string; text: ToolDetailText; diff?: boolean }) {
  const notice = truncationNotice(text);
  return <div className="min-w-0" data-detail-log={label.toLowerCase()}>
    <div className="flex min-h-8 items-center justify-between text-xs text-muted-foreground">
      <span>{label}</span>
      {text.text ? <CopyButton text={text.text} label={`Copy ${label.toLowerCase()}`} /> : null}
    </div>
    {text.text
      ? <pre tabIndex={0} role="region" aria-label={label} className="max-h-64 max-w-full overflow-auto overscroll-contain rounded-lg border bg-muted/40 p-3 font-mono text-xs leading-5 text-foreground">
        <code>{diff
          ? text.text.split("\n").map((line, index) => {
            const kind = diffLineKind(line);
            return <span key={index} data-diff-line={kind} className={`block whitespace-pre ${kind === "add" ? "text-emerald-700 dark:text-emerald-300" : kind === "del" ? "text-red-700 dark:text-red-300" : kind === "hunk" ? "text-muted-foreground" : ""}`}>{line || " "}</span>;
          })
          : <span className="block whitespace-pre">{text.text}</span>}</code>
      </pre>
      : <p className="text-xs text-muted-foreground">Nothing was recorded.</p>}
    {notice ? <p className="mt-1 text-xs text-muted-foreground" data-detail-truncated>{notice}</p> : null}
  </div>;
}

function Downloads({ taskId, artifacts }: { taskId: string; artifacts: ToolCallDetail["artifacts"] }) {
  const links = artifacts.flatMap((artifact) => {
    const href = safeDownloadHref(taskId, artifact.download_url);
    return href ? [{ artifact, href }] : [];
  });
  if (!links.length) return null;
  return <ul className="space-y-1" aria-label="Files">
    {links.map(({ artifact, href }) => <li key={artifact.id}>
      <a href={href} className={linkClass}><Download className="size-3.5 shrink-0" aria-hidden="true" /><span className="min-w-0 break-all">{artifact.filename}</span><span className="shrink-0 font-normal text-muted-foreground">{formatBytes(artifact.byte_size)}</span></a>
    </li>)}
  </ul>;
}

function Ready({ taskId, detail, onRetry }: { taskId: string; detail: ToolCallDetail; onRetry: () => void }) {
  const note = unavailableMessage(detail.unavailable_reason);
  const error = errorText(detail.error_code);
  const anything = !!(detail.execution || detail.stdout || detail.stderr || detail.diff || detail.preview || detail.files.length || detail.artifacts.length);
  return <div className="space-y-3">
    {detail.kind === "execution" ? <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm" data-detail-summary>
      {executionRows(detail).map((row) => <div key={row.label} className="contents"><dt className="text-muted-foreground">{row.label}</dt><dd className="min-w-0 break-words">{row.value}</dd></div>)}
    </dl> : null}
    {error ? <p className="text-sm" data-detail-error>{error}</p> : null}
    {detail.kind === "diff" && detail.files.length ? <div className="space-y-1">
      <p className="text-sm text-muted-foreground">{diffTotals(detail.files)}</p>
      <ul className="space-y-0.5 text-sm" aria-label="Changed files">
        {detail.files.map((file) => <li key={file.path} className="flex min-w-0 items-baseline gap-3"><span className="min-w-0 break-all font-mono text-xs">{file.path}</span>
          {file.additions !== null || file.deletions !== null ? <span className="shrink-0 font-mono text-xs text-muted-foreground"><span className="text-emerald-700 dark:text-emerald-300">+{file.additions ?? 0}</span> <span className="text-red-700 dark:text-red-300">−{file.deletions ?? 0}</span></span> : null}</li>)}
      </ul>
    </div> : null}
    {detail.stdout ? <LogBlock label="Output" text={detail.stdout} /> : null}
    {detail.stderr?.text ? <LogBlock label="Errors" text={detail.stderr} /> : null}
    {detail.diff ? <LogBlock label="Changes" text={detail.diff} diff /> : null}
    {detail.preview ? <LogBlock label="Preview" text={detail.preview} /> : null}
    {detail.tool !== "create_task_artifact" ? <Downloads taskId={taskId} artifacts={detail.artifacts} /> : null}
    {note ? <p className="text-sm text-muted-foreground" data-detail-note>{note}{detail.unavailable_reason && retryableReasons.has(detail.unavailable_reason) ? <> <button type="button" className={retryClass} onClick={onRetry}>Retry</button></> : null}</p>
      : !anything ? <p className="text-sm text-muted-foreground">No further details were recorded.</p> : null}
  </div>;
}

/**
 * The details of one finished execution, diff or artifact call. It is mounted only while its row is open, so nothing is
 * requested for rows nobody opened and closing the row (or leaving the page) aborts the request. Mount it with a `key` of the
 * call, since it loads for the call it was created with.
 */
export function ToolDetailPanel({ taskId, toolCallId }: { taskId: string; toolCallId: string }) {
  const [state, dispatch] = useReducer(detailReducer, undefined, () => initialDetailState(loader.peek(taskId, toolCallId)));
  const attempt = state.status === "loading" ? state.attempt : 0;
  useEffect(() => {
    if (!attempt) return;
    const controller = new AbortController();
    loader.load(taskId, toolCallId, controller.signal).then(
      (detail) => { if (!controller.signal.aborted) dispatch({ type: "loaded", attempt, detail }); },
      (error: unknown) => { if (!controller.signal.aborted) dispatch({ type: "failed", attempt, failure: classifyFailure(error) }); },
    );
    return () => controller.abort();
  }, [taskId, toolCallId, attempt]);
  const retry = () => dispatch({ type: "retry" });
  return <div data-tool-detail data-state={state.status} className="text-sm">
    {state.status === "loading" ? <p role="status" className="text-muted-foreground">Loading details…</p>
      : state.status === "ready" ? <Ready taskId={taskId} detail={state.detail} onRetry={retry} />
        : state.status === "unavailable" ? <p className="text-muted-foreground">{state.message}</p>
          : <p role="alert" className="text-muted-foreground">{state.message} <button type="button" className={retryClass} onClick={retry}>Retry</button></p>}
  </div>;
}
