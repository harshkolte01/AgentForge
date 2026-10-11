/**
 * What the Delivery panel says, decided in one place and free of React: the headline and its one primary action (the table in
 * `docs/delivery.md`), what each action is allowed to do right now, check ordering, the canned prompts that ask the agent for
 * help, and the unified-diff parser. Everything here is a pure function of the server's `DeliveryView` and `ChangesView`.
 */
import type { BlockedReason, CheckItem, ChangesView, DeliveryView, MergeMethod, PullRequest } from "@/lib/delivery-api";

export const plural = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;

export type Tone = "green" | "yellow" | "red" | "purple" | "muted";
export type ActionKind = "continue" | "create_pull_request" | "push_update" | "resolve" | "fix_checks" | "ready_for_review" | "address_feedback" | "update_branch" | "merge";
export type HeadlineKey = "merged" | "closed" | "changed" | "unpushed" | "conflicts" | "checks_failed" | "checks_pending" | "draft" | "changes_requested" | "behind" | "ready" | "branch" | "empty";
export type Headline = { key: HeadlineKey; label: string; tone: Tone; action: ActionKind | null; actionLabel: string | null };
/** What is unpublished in the sandbox; null while it has not been read. */
export type ChangeSummary = { files: number; additions: number; deletions: number } | null;

export const actionLabels: Record<ActionKind, string> = {
  continue: "Continue",
  create_pull_request: "Create pull request",
  push_update: "Push update",
  resolve: "Resolve",
  fix_checks: "Fix checks",
  ready_for_review: "Ready for review",
  address_feedback: "Address feedback",
  update_branch: "Update branch",
  merge: "Merge",
};
/** These ask the agent to do the work; the person still pushes from the panel. */
export const agentActions: ReadonlySet<ActionKind> = new Set(["resolve", "fix_checks", "address_feedback"]);

export function summarizeChanges(changes: ChangesView | null): ChangeSummary {
  return changes ? { files: changes.files.length, additions: changes.additions, deletions: changes.deletions } : null;
}

/**
 * What is unpublished, for the card, chip and headline. The delivery's own summary needs no sandbox read, so it is what shows
 * until the detail has been opened; once the Changes detail is in view (`preferDetail`) its exact count wins.
 */
export function unpublishedSummary(view: DeliveryView | null, changes: ChangesView | null, preferDetail: boolean): ChangeSummary {
  if (preferDetail && changes) return summarizeChanges(changes);
  if (view?.unpublished) return { files: view.unpublished.files, additions: view.unpublished.additions, deletions: view.unpublished.deletions };
  return summarizeChanges(changes);
}

export const blockedLabels: Record<BlockedReason, string> = { secret: "Looks like a secret", path: "Unsafe path", submodule: "Submodule", symlink: "Symlink" };
export const blockedFiles = (changes: ChangesView | null) => (changes?.files ?? []).filter((file) => file.blocked);
/** The one sentence for "files that cannot be published are in the changes", shared by the note and the disabled button. */
export const blockedReason = (count: number) => `${plural(count, "changed file")} can't be published. Discard ${count === 1 ? "it" : "them"} to continue`;

export const isOpen = (pr: PullRequest | null): pr is PullRequest => !!pr && (pr.state === "open" || pr.state === "draft");

/** The status line and its single primary action, in the precedence order of the contract's table. */
export function deriveHeadline(view: DeliveryView, changes: ChangeSummary): Headline {
  const pr = view.pull_request;
  const unpublished = changes?.files ?? 0;
  const make = (key: HeadlineKey, label: string, tone: Tone, action: ActionKind | null): Headline => ({ key, label, tone, action, actionLabel: action ? actionLabels[action] : null });
  if (pr?.state === "merged") return make("merged", "Merged", "purple", "continue");
  if (pr?.state === "closed") return make("closed", "Closed", "muted", "continue");
  if (unpublished > 0 && !pr) return make("changed", `${plural(unpublished, "file")} changed`, "yellow", "create_pull_request");
  if (unpublished > 0 && pr) return make("unpushed", `${plural(unpublished, "unpushed file")}`, "yellow", "push_update");
  if (pr) {
    if (pr.mergeable === "conflicting") return make("conflicts", "Merge conflicts", "red", "resolve");
    if (pr.checks.state === "failing") return make("checks_failed", "Checks failed", "red", "fix_checks");
    if (pr.checks.state === "pending") return make("checks_pending", `${plural(Math.max(pr.checks.pending, 1), "check")} pending…`, "yellow", null);
    if (pr.state === "draft") return make("draft", "Draft", "muted", "ready_for_review");
    if (pr.reviews.decision === "changes_requested") return make("changes_requested", "Changes requested", "red", "address_feedback");
    if (pr.behind_base !== null && pr.behind_base > 0) return make("behind", `${plural(pr.behind_base, "commit")} behind ${view.base_branch ?? "base"}`, "yellow", "update_branch");
    return make("ready", "Ready to merge", "green", "merge");
  }
  // A branch exists but nothing is open for review: the next step is still a pull request (an addition to the table).
  if (view.state === "branch" || view.branch) return make("branch", "Branch published", "muted", "create_pull_request");
  return make("empty", "No changes yet", "muted", null);
}

/** Actions that are useful but not the headline's one primary action. */
export function secondaryActions(view: DeliveryView, headline: Headline): ActionKind[] {
  const pr = view.pull_request;
  if (!isOpen(pr) || headline.key === "unpushed") return [];
  const found: ActionKind[] = [];
  if (pr.state === "draft" && headline.action !== "ready_for_review") found.push("ready_for_review");
  if (pr.behind_base !== null && pr.behind_base > 0 && headline.action !== "update_branch") found.push("update_branch");
  return found;
}

/** The short form for the header chip. */
export function chipLabel(view: DeliveryView, changes: ChangeSummary): { label: string; tone: Tone } {
  const headline = deriveHeadline(view, changes);
  const pr = view.pull_request;
  if (!pr) {
    if (headline.key === "changed") return { label: plural(changes?.files ?? 0, "unpublished file"), tone: headline.tone };
    return { label: headline.label, tone: headline.tone };
  }
  const short: Partial<Record<HeadlineKey, string>> = {
    merged: "Merged", closed: "Closed", unpushed: plural(changes?.files ?? 0, "unpushed file"), conflicts: "Conflicts", checks_failed: "Checks failed",
    checks_pending: "Checks running", draft: "Draft", changes_requested: "Changes requested", behind: "Behind base", ready: "Ready to merge",
  };
  return { label: `PR #${pr.number} · ${short[headline.key] ?? headline.label}`, tone: headline.tone };
}

export type Gate = { enabled: boolean; reason: string | null };
export type GatedAction = ActionKind | "close" | "discard";
// Discarding a file only touches the sandbox, so it needs no GitHub write access (a secret in a read-only repo must still go).
const writes: ReadonlySet<GatedAction> = new Set(["create_pull_request", "push_update", "ready_for_review", "update_branch", "merge", "close"]);

/** Whether an action may be taken right now, and if not, the sentence that says why. */
export function gateAction(action: GatedAction, view: DeliveryView, changes: ChangesView | null): Gate {
  const refuse = (reason: string): Gate => ({ enabled: false, reason });
  const busyReason = "The agent is working…";
  if (view.pending_operation) return refuse("Confirming with GitHub…");
  if (action === "continue") return view.busy ? refuse(busyReason) : { enabled: true, reason: null };
  if (agentActions.has(action as ActionKind)) return view.busy ? refuse(busyReason) : { enabled: true, reason: null };
  if (writes.has(action)) {
    if (!view.capabilities.writes_enabled) return refuse("Publishing is turned off on this server");
    if (view.capabilities.can_write === false) return refuse(`You only have read access to ${view.repo_id ?? "this repository"}`);
    if (view.sync_error === "github_reauthorization_required") return refuse("Reconnect GitHub to continue");
  }
  const touchesWorkspace = action === "push_update" || action === "discard" || action === "create_pull_request";
  if (touchesWorkspace && view.busy) return refuse(busyReason);
  const blockedCount = blockedFiles(changes).length;
  if ((action === "push_update" || action === "create_pull_request") && blockedCount) return refuse(blockedReason(blockedCount));
  const expired = changes?.workspace === "expired";
  if (expired && (action === "push_update" || action === "discard" || (action === "create_pull_request" && view.state === "none"))) {
    return refuse("The sandbox expired, so there is nothing left to publish");
  }
  if (action === "merge" && !(view.pull_request?.merge_methods.length)) return refuse("This repository allows no merge method");
  return { enabled: true, reason: null };
}

export type Notice = { id: string; tone: "info" | "warn"; text: string; link?: { label: string; href: string } };

/** Standing conditions that explain why something is missing or disabled. */
export function deliveryNotices(view: DeliveryView, changes: ChangesView | null): Notice[] {
  const notices: Notice[] = [];
  if (!view.capabilities.writes_enabled) notices.push({ id: "writes", tone: "info", text: "Publishing is turned off on this server" });
  else if (view.capabilities.can_write === false) notices.push({ id: "read-only", tone: "info", text: `You only have read access to ${view.repo_id ?? "this repository"}` });
  if (view.pull_request && (view.capabilities.checks_readable === false || view.pull_request.checks.state === "unavailable")) {
    notices.push({ id: "checks", tone: "info", text: "Checks unavailable — grant the App Checks:read" });
  }
  if (changes?.workspace === "expired") notices.push({ id: "expired", tone: "warn", text: "This conversation's sandbox has expired. Edits that were not published are lost." });
  else if (changes?.workspace === "unavailable") notices.push({ id: "workspace", tone: "warn", text: "The sandbox can't be read right now, so unpublished changes may not be shown." });
  const blockedCount = blockedFiles(changes).length;
  if (blockedCount) notices.push({ id: "blocked", tone: "warn", text: blockedReason(blockedCount) });
  if (view.busy) notices.push({ id: "busy", tone: "info", text: "The agent is working…" });
  if (view.pending_operation) notices.push({ id: "pending", tone: "info", text: "Confirming with GitHub…" });
  return notices;
}

/** Whether a notice already says this (so the reason is not printed a second time beside the control it disables). */
export const isExplained = (notices: readonly Notice[], reason: string | null) => !!reason && notices.some((notice) => notice.text === reason);

const checkRank: Record<CheckItem["status"], number> = { failed: 0, pending: 1, passed: 2, neutral: 3 };
/** Failing first, then pending, then passed and neutral; names keep their order inside a group. */
export function sortChecks(items: readonly CheckItem[]): CheckItem[] {
  return items.map((item, index) => ({ item, index })).sort((a, b) => checkRank[a.item.status] - checkRank[b.item.status] || a.index - b.index).map(({ item }) => item);
}
export function checksSummary(checks: PullRequest["checks"]): string {
  const parts = [checks.failed ? `${checks.failed} failed` : "", checks.pending ? `${checks.pending} pending` : "", checks.passed ? `${checks.passed} passed` : ""].filter(Boolean);
  return parts.join(" · ");
}

export const methodLabels: Record<MergeMethod, string> = { squash: "Squash and merge", merge: "Create a merge commit", rebase: "Rebase and merge" };
const methodVerbs: Record<MergeMethod, string> = { squash: "Squash and merge", merge: "Merge", rebase: "Rebase and merge" };
/** The exact thing the Merge button will do, for its confirmation line. */
export function mergeSentence(method: MergeMethod, number: number, base: string | null): string {
  return `${methodVerbs[method]} #${number} into ${base ?? "the base branch"}`;
}
/** The remembered method when the repository still allows it; otherwise squash, merge, rebase in that order. */
export function pickMergeMethod(allowed: readonly MergeMethod[], preferred?: string | null): MergeMethod | null {
  if (preferred && (allowed as readonly string[]).includes(preferred)) return preferred as MergeMethod;
  return (["squash", "merge", "rebase"] as const).find((method) => allowed.includes(method)) ?? null;
}

/** The messages that ask the agent for help. The agent cannot push: the person does that from the panel. */
export function agentPrompt(action: "fix_checks" | "resolve" | "address_feedback", view: DeliveryView): string {
  const number = view.pull_request?.number;
  if (action === "fix_checks") return `Investigate the failing checks on PR #${number}, fix the failures, run the relevant tests, and tell me when the changes are ready to push.`;
  if (action === "resolve") return `The pull request has merge conflicts with ${view.base_branch ?? "the base branch"}. Look at what changed and resolve it in the workspace, then tell me when the changes are ready to push.`;
  return `Address the requested changes on PR #${number}, run the relevant tests, and tell me when the changes are ready to push.`;
}

export const shortSha = (sha: string) => sha.slice(0, 7);
export const firstLine = (text: string) => text.split("\n", 1)[0].trim();
export function splitPath(path: string): { dir: string; name: string } {
  const at = path.lastIndexOf("/");
  return at < 0 ? { dir: "", name: path } : { dir: path.slice(0, at + 1), name: path.slice(at + 1) };
}
/** Links come from GitHub through our API; only plain https ones are rendered as links. */
export function safeHref(url: string | null | undefined): string | undefined {
  try { const parsed = new URL(url ?? ""); return parsed.protocol === "https:" && !parsed.username && !parsed.password ? parsed.href : undefined; }
  catch { return undefined; }
}
export function relativeTime(iso: string | null, now = Date.now()): string {
  const then = iso ? Date.parse(iso) : NaN;
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  return hours < 24 ? `${hours}h ago` : `${Math.round(hours / 24)}d ago`;
}

/** A short, stable fingerprint of a patch (cyrb53), so "reviewed" can be forgotten when the file's diff changes. */
export function hashText(text: string): string {
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
  for (let index = 0; index < text.length; index++) {
    const code = text.charCodeAt(index);
    h1 = Math.imul(h1 ^ code, 2654435761);
    h2 = Math.imul(h2 ^ code, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
}

export type DiffRow =
  | { kind: "hunk"; text: string }
  | { kind: "add" | "del" | "ctx"; text: string; oldLine: number | null; newLine: number | null }
  | { kind: "note"; text: string };

const hunkHeader = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;

/**
 * Reads one file's unified diff. File headers (`diff --git`, `index`, `---`, `+++`) are skipped; inside a hunk the header's line
 * counts say where the hunk ends, so a removed line that starts with `--` is never mistaken for a header. A patch cut short by the
 * server simply ends early.
 */
export function parsePatch(patch: string): DiffRow[] {
  const rows: DiffRow[] = [];
  if (!patch) return rows;
  const lines = patch.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  let oldLine = 0, newLine = 0, oldLeft = 0, newLeft = 0;
  for (const raw of lines) {
    const line = raw.endsWith("\r") ? raw.slice(0, -1) : raw;
    if (oldLeft <= 0 && newLeft <= 0) {
      const header = hunkHeader.exec(line);
      if (header) {
        oldLine = Number(header[1]); newLine = Number(header[3]);
        oldLeft = header[2] === undefined ? 1 : Number(header[2]);
        newLeft = header[4] === undefined ? 1 : Number(header[4]);
        rows.push({ kind: "hunk", text: line });
      } else if (line.startsWith("\\") && rows.length) rows.push({ kind: "note", text: line.slice(1).trim() });
      continue;
    }
    const mark = line[0];
    if (mark === "\\") rows.push({ kind: "note", text: line.slice(1).trim() });
    else if (mark === "+") { rows.push({ kind: "add", text: line.slice(1), oldLine: null, newLine: newLine++ }); newLeft--; }
    else if (mark === "-") { rows.push({ kind: "del", text: line.slice(1), oldLine: oldLine++, newLine: null }); oldLeft--; }
    else { rows.push({ kind: "ctx", text: line.slice(1), oldLine: oldLine++, newLine: newLine++ }); oldLeft--; newLeft--; }
  }
  return rows;
}
