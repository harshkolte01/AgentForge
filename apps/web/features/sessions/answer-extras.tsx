"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { Download, FileText } from "lucide-react";
import { deliverableViews, type DeliverableView } from "@/features/sessions/answer-extras-model";
import { api, message, type Artifact, type MessageActivity } from "@/lib/task-api";

const actionClass = "inline-flex min-h-6 items-center rounded px-1 text-xs font-medium text-foreground underline underline-offset-4 outline-none focus-visible:ring-2 focus-visible:ring-ring/60 [@media(pointer:coarse)]:min-h-11";

const chipClass = "inline-flex max-w-full items-center gap-2 rounded-lg border bg-background px-2.5 py-1.5 text-[13px] leading-5 text-foreground";

function DeliverableList({ views }: { views: readonly DeliverableView[] }) {
  return <section aria-label="Files" className="min-w-0" data-answer-files>
    <ul className="flex flex-wrap gap-2">
      {views.map((view) => <li key={view.id} className="min-w-0 max-w-full">
        {view.href
          ? <a href={view.href} className={`${chipClass} outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/60 motion-reduce:transition-none [@media(pointer:coarse)]:min-h-11`}><Download className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" /><span className="min-w-0 truncate">{view.filename}</span>{view.size ? <span className="shrink-0 text-xs text-muted-foreground">{view.size}</span> : null}</a>
          : <span className={chipClass}><FileText className="size-3.5 shrink-0 text-muted-foreground" aria-hidden="true" /><span className="min-w-0 truncate">{view.filename}</span></span>}
      </li>)}
    </ul>
  </section>;
}

type Loaded<T> = { status: "loading" } | { status: "error"; message: string } | { status: "ready"; items: T };

/** The files of a finished reply, kept for the page session so a re-render never asks the server again. */
const artifactCache = new Map<string, Artifact[]>();

/** Becomes true once the element is near the viewport (or at once where IntersectionObserver is missing). */
function useNear(): [ref: (node: Element | null) => void, near: boolean] {
  const [near, setNear] = useState(typeof IntersectionObserver === "undefined");
  const observer = useRef<IntersectionObserver | null>(null);
  useEffect(() => () => observer.current?.disconnect(), []);
  const ref = useCallback((node: Element | null) => {
    observer.current?.disconnect();
    if (!node || near || typeof IntersectionObserver === "undefined") return;
    observer.current = new IntersectionObserver((hits) => { if (hits.some((hit) => hit.isIntersecting)) { setNear(true); observer.current?.disconnect(); } }, { rootMargin: "240px" });
    observer.current.observe(node);
  }, [near]);
  return [ref, near];
}

function useRecords<T>(enabled: boolean, cache: Map<string, T>, key: string, path: string): [Loaded<T>, () => void] {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; value: Loaded<T> } | null>(null);
  const cached = cache.get(key);
  const requestKey = `${key}:${attempt}`;
  useEffect(() => {
    if (!enabled || cached) return;
    const controller = new AbortController();
    api<T>(path, { signal: controller.signal }).then(
      (items) => {
        cache.set(key, items);
        if (!controller.signal.aborted) setResult({ key: requestKey, value: { status: "ready", items } });
      },
      (error: unknown) => { if (!controller.signal.aborted) setResult({ key: requestKey, value: { status: "error", message: message(error) } }); },
    );
    return () => controller.abort();
  }, [enabled, cached, cache, key, path, requestKey]);
  const value: Loaded<T> = cached ? { status: "ready", items: cached } : result?.key === requestKey ? result.value : { status: "loading" };
  return [value, () => setAttempt((count) => count + 1)];
}

/**
 * Under an answer: the files it produced. Files are read from the owner-scoped task route only when the message's count says
 * there are some, and only once the block is near the screen, so a long history does not fire a request per reply. Renders
 * nothing for an answer with no files.
 */
export function AnswerExtras({ taskId, activity }: { taskId: string; activity?: MessageActivity | null }) {
  const [ref, near] = useNear();
  const wantsFiles = !!activity && activity.artifact_count > 0;
  const [files, retryFiles] = useRecords<Artifact[]>(near && wantsFiles, artifactCache, taskId, `/tasks/${encodeURIComponent(taskId)}/artifacts`);
  if (!wantsFiles) return null;
  const fileItems = files.status === "ready" ? deliverableViews(taskId, files.items) : [];
  return <div ref={ref} className="mt-3 flex min-w-0 flex-col gap-3" data-answer-extras>
    {wantsFiles && near ? (files.status === "loading" ? <p role="status" className="text-xs text-muted-foreground">Loading files…</p>
      : files.status === "error" ? <p role="alert" className="text-xs text-muted-foreground">Couldn&apos;t load the files. <button type="button" className={actionClass} onClick={retryFiles}>Retry</button></p>
        : fileItems.length ? <DeliverableList views={fileItems} /> : null) : null}
  </div>;
}
