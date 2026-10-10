"use client";

import { useEffect, useState, type FormEvent } from "react";
import { Check, Download, GitPullRequest, RefreshCw, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { inputClass } from "@/components/session-boundary";
import { api, formatDate, message, publicSourceUrl, statusLabel } from "@/lib/task-api";
import type { Approval } from "@/lib/github-api";

export function ChangeApprovals({ taskId }: { taskId: string }) {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    void api<{ approvals: Approval[] }>(`/tasks/${taskId}/approvals`, { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) { setApprovals(result.approvals); setLoaded(true); setError(""); }
    }).catch((error) => { if (!controller.signal.aborted) { setError(message(error)); setLoaded(true); } });
    return () => controller.abort();
  }, [taskId, refresh]);
  async function create(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const approval = await api<Approval>(`/tasks/${taskId}/approvals`, { method: "POST", body: JSON.stringify({ title: title.trim(), body: body.trim() }) });
      setApprovals((current) => [approval, ...current.filter((item) => item.id !== approval.id)]); setTitle(""); setBody("");
    } catch (error) { setError(message(error)); } finally { setBusy(false); }
  }
  return <section className="min-w-0 space-y-5 border-t pt-6" aria-labelledby={`approvals-${taskId}`}>
    <div className="flex items-center justify-between gap-3"><h2 id={`approvals-${taskId}`} className="flex items-center gap-2 text-base font-semibold"><GitPullRequest className="size-4" aria-hidden="true" />Publish a change</h2><Button variant="ghost" size="icon" aria-label="Refresh approvals" onClick={() => setRefresh((value) => value + 1)}><RefreshCw /></Button></div>
    <p className="max-w-prose text-sm leading-6 text-muted-foreground">Preview the exact patch, repository and branch before approving. Publishing creates a task branch and pull request; approval is single use and expires.</p>
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
    {!loaded ? <p role="status" className="text-sm text-muted-foreground">Loading approvals…</p> : null}
    {approvals.map((approval) => <ApprovalPreview key={`${approval.id}-${approval.state}-${approval.action_hash}`} initial={approval} />)}
    <details className="group"><summary className="cursor-pointer text-sm font-medium underline-offset-4 hover:underline focus-visible:outline-2">Prepare a pull request</summary><form onSubmit={create} className="mt-5 space-y-4"><div className="space-y-2"><label htmlFor={`pr-title-${taskId}`} className="text-sm font-medium">Pull request title</label><input id={`pr-title-${taskId}`} value={title} onChange={(event) => setTitle(event.target.value)} required minLength={3} maxLength={200} className={inputClass} disabled={busy} /></div><div className="space-y-2"><label htmlFor={`pr-body-${taskId}`} className="text-sm font-medium">Description</label><textarea id={`pr-body-${taskId}`} value={body} onChange={(event) => setBody(event.target.value)} maxLength={10000} rows={4} className={inputClass} disabled={busy} /></div><p className="text-xs leading-5 text-muted-foreground">The server selects the verified revision and task branch. Repository access and write permissions are checked again before publishing.</p><Button type="submit" variant="outline" disabled={busy || !loaded}>{busy ? "Preparing…" : "Preview exact change"}</Button></form></details>
  </section>;
}

function ApprovalPreview({ initial }: { initial: Approval }) {
  const [approval, setApproval] = useState(initial);
  const [patch, setPatch] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [readPatch, setReadPatch] = useState(false);
  const [patchBusy, setPatchBusy] = useState(false);
  const action = approval.action;
  const diffUrl = `/api/v1/tasks/${approval.task_id}/artifacts/${action.diff_artifact_id}`;
  const [expired, setExpired] = useState(false);
  useEffect(() => {
    const check = () => setExpired(new Date(approval.expires_at).getTime() <= Date.now());
    const timeout = window.setTimeout(check, 0);
    const timer = window.setInterval(check, 1000);
    return () => { window.clearTimeout(timeout); window.clearInterval(timer); };
  }, [approval.expires_at]);
  async function loadPatch() {
    setError(""); setPatchBusy(true);
    try {
      const response = await fetch(diffUrl, { credentials: "same-origin", cache: "no-store" });
      if (!response.ok) throw new Error("The approved patch is unavailable. Refresh this approval before continuing.");
      const value = await response.text();
      setPatch(value.slice(0, 100000));
    } catch (error) { setError(message(error)); } finally { setPatchBusy(false); }
  }
  async function act(decision?: "approve" | "deny") {
    setBusy(true); setError("");
    try {
      const result = await api<Approval>(`/approvals/${approval.id}/${decision ? "decision" : "execute"}`, { method: "POST", ...(decision ? { body: JSON.stringify({ action_hash: approval.action_hash, decision }) } : {}) });
      setApproval(result);
    } catch (error) { setError(message(error)); } finally { setBusy(false); }
  }
  const resultUrl = typeof approval.result?.pr_url === "string" ? publicSourceUrl(approval.result.pr_url) : undefined;
  return <article className="min-w-0 space-y-4 border-y py-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="break-words text-sm font-semibold">{action.title}</h3><Badge variant="outline">{expired && approval.state === "PENDING" ? "Expired" : statusLabel(approval.state)}</Badge></div>
    <dl className="grid gap-x-5 gap-y-3 text-xs sm:grid-cols-[7rem_minmax(0,1fr)]"><dt className="text-muted-foreground">Repository</dt><dd className="break-all">{action.repo_id}</dd><dt className="text-muted-foreground">Branches</dt><dd className="break-all">{action.head_branch} → {action.base_branch}</dd><dt className="text-muted-foreground">Base commit</dt><dd className="break-all font-mono">{action.base_sha}</dd><dt className="text-muted-foreground">Patch SHA-256</dt><dd className="break-all font-mono">{action.diff_sha256}</dd><dt className="text-muted-foreground">Action SHA-256</dt><dd className="break-all font-mono">{approval.action_hash}</dd><dt className="text-muted-foreground">Expires</dt><dd>{formatDate(approval.expires_at)}</dd><dt className="text-muted-foreground">Operations</dt><dd className="break-words">{action.requested_operations.join(", ")}</dd></dl>
    {action.body ? <p className="whitespace-pre-wrap break-words text-sm leading-6">{action.body}</p> : null}
    <div className="flex flex-wrap items-center gap-4"><Button variant="outline" size="sm" onClick={() => void loadPatch()} disabled={patchBusy}>{patchBusy ? "Loading patch…" : "Read exact patch"}</Button><a href={diffUrl} className="inline-flex items-center gap-1 text-xs underline underline-offset-4 focus-visible:outline-2"><Download className="size-3" aria-hidden="true" />Download full patch</a></div>
    {patch !== null ? <div className="space-y-2"><pre className="max-h-96 overflow-auto rounded-lg bg-muted/50 p-4 font-mono text-xs leading-6" tabIndex={0} aria-label="Exact change patch">{patch || "The patch is empty."}</pre><p className="text-xs text-muted-foreground">Preview limited to 100,000 characters. The approval binds the full downloadable patch hash.</p></div> : null}
    {approval.state === "PENDING" && !expired ? <div className="space-y-4"><label className="flex items-start gap-2 text-sm leading-6"><input type="checkbox" checked={readPatch} onChange={(event) => setReadPatch(event.target.checked)} disabled={patch === null || busy} className="mt-1 size-4 accent-primary" />I reviewed the patch and approve these exact operations on the branches above.</label><div className="flex flex-wrap gap-2"><Button disabled={!readPatch || busy} onClick={() => void act("approve")}><Check />Approve exact change</Button><Button variant="outline" disabled={busy} onClick={() => void act("deny")}><X />Deny</Button></div></div> : null}
    {(approval.state === "APPROVED" && !expired) || approval.state === "EXECUTING" ? <div className="space-y-3"><p className="text-sm text-muted-foreground">{approval.state === "EXECUTING" ? "Publication has an unresolved outcome. Reconcile it with GitHub before taking further action." : "Approved. Publish once to create the task branch and draft pull request."}</p><Button disabled={busy} onClick={() => void act()}>{busy ? "Checking publication…" : approval.state === "EXECUTING" ? "Reconcile publication" : "Publish approved draft pull request"}</Button></div> : null}
    {resultUrl ? <a href={resultUrl} target="_blank" rel="noopener noreferrer" className="inline-flex text-sm font-medium underline underline-offset-4 focus-visible:outline-2">Open pull request on GitHub</a> : null}
    {approval.result ? <details className="text-xs"><summary className="cursor-pointer focus-visible:outline-2">Publication result</summary><pre className="mt-3 max-h-64 overflow-auto rounded-lg bg-muted/50 p-4" tabIndex={0}>{JSON.stringify(approval.result, null, 2)}</pre></details> : null}
    {error ? <p role="alert" className="text-sm text-destructive">{error}</p> : null}
  </article>;
}
