import { test } from "node:test";
import assert from "node:assert/strict";
import {
  agentPrompt, blockedFiles, blockedReason, unpublishedSummary, chipLabel, deriveHeadline, deliveryNotices, gateAction, hashText, mergeSentence, parsePatch, pickMergeMethod, relativeTime, safeHref,
  secondaryActions, sortChecks, splitPath,
} from "../lib/delivery-status.ts";

const pull = (overrides = {}) => ({
  number: 12, url: "https://github.com/o/r/pull/12", title: "Add things", state: "open", head_sha: "a".repeat(40), mergeable: "mergeable", behind_base: 0,
  checks: { state: "passing", passed: 3, failed: 0, pending: 0, items: [] },
  reviews: { decision: "none", items: [] }, merged_at: null, merge_methods: ["squash", "merge"], ...overrides,
});
const delivery = (overrides = {}) => ({
  available: true, unavailable_reason: null, capabilities: { writes_enabled: true, can_write: true, checks_readable: true }, busy: false, repo_id: "o/r",
  base_branch: "main", base_sha: "b".repeat(40), branch: "agentforge/x-1", branch_url: "https://github.com/o/r/tree/agentforge/x-1", head_sha: "a".repeat(40),
  state: "pull_request", pull_request: pull(), commits: [], pending_operation: null, synced_at: null, sync_error: null, version: 1, ...overrides,
});
const files = (count) => ({ files: count, additions: 40, deletions: 5 });
const withPr = (prOverrides, viewOverrides = {}) => delivery({ pull_request: pull(prOverrides), ...viewOverrides });

test("the headline follows the contract's precedence, one primary action each", () => {
  const cases = [
    [withPr({ state: "merged" }), files(2), "Merged", "continue"],
    [withPr({ state: "closed" }), files(0), "Closed", "continue"],
    [delivery({ state: "none", pull_request: null }), files(3), "3 files changed", "create_pull_request"],
    [delivery({ state: "branch", pull_request: null }), files(1), "1 file changed", "create_pull_request"],
    [withPr({}), files(2), "2 unpushed files", "push_update"],
    [withPr({ mergeable: "conflicting", checks: { state: "failing", passed: 0, failed: 1, pending: 0, items: [] } }), files(0), "Merge conflicts", "resolve"],
    [withPr({ checks: { state: "failing", passed: 2, failed: 1, pending: 1, items: [] } }), files(0), "Checks failed", "fix_checks"],
    [withPr({ checks: { state: "pending", passed: 1, failed: 0, pending: 2, items: [] } }), files(0), "2 checks pending…", null],
    [withPr({ state: "draft" }), files(0), "Draft", "ready_for_review"],
    [withPr({ reviews: { decision: "changes_requested", items: [] } }), files(0), "Changes requested", "address_feedback"],
    [withPr({ behind_base: 3 }), files(0), "3 commits behind main", "update_branch"],
    [withPr({ behind_base: 1 }), files(0), "1 commit behind main", "update_branch"],
    [withPr({}), files(0), "Ready to merge", "merge"],
    [delivery({ state: "none", pull_request: null, branch: null }), files(0), "No changes yet", null],
  ];
  for (const [view, changes, label, action] of cases) {
    const headline = deriveHeadline(view, changes);
    assert.equal(headline.label, label);
    assert.equal(headline.action, action, label);
  }
});

test("a draft with failing checks asks to fix the checks first, and a draft behind its base is not 'ready to merge'", () => {
  assert.equal(deriveHeadline(withPr({ state: "draft", checks: { state: "failing", passed: 0, failed: 1, pending: 0, items: [] } }), files(0)).action, "fix_checks");
  assert.equal(deriveHeadline(withPr({ state: "draft", behind_base: 2 }), files(0)).key, "draft");
});

test("tones: merged is purple, trouble is red, waiting is yellow, ready is green", () => {
  assert.equal(deriveHeadline(withPr({ state: "merged" }), files(0)).tone, "purple");
  assert.equal(deriveHeadline(withPr({ mergeable: "conflicting" }), files(0)).tone, "red");
  assert.equal(deriveHeadline(withPr({ checks: { state: "pending", passed: 0, failed: 0, pending: 1, items: [] } }), files(0)).tone, "yellow");
  assert.equal(deriveHeadline(withPr({}), files(0)).tone, "green");
  assert.equal(deriveHeadline(withPr({ state: "closed" }), files(0)).tone, "muted");
});

test("a published branch with no pull request still offers one, and unknown changes count as none", () => {
  const branch = deriveHeadline(delivery({ state: "branch", pull_request: null }), files(0));
  assert.deepEqual([branch.key, branch.action], ["branch", "create_pull_request"]);
  assert.equal(deriveHeadline(delivery({ state: "none", pull_request: null, branch: null }), null).key, "empty");
});

test("secondary actions are the useful ones that are not the primary", () => {
  const draftBehind = withPr({ state: "draft", behind_base: 2 });
  assert.deepEqual(secondaryActions(draftBehind, deriveHeadline(draftBehind, files(0))), ["update_branch"]);
  const readyBehind = withPr({ state: "draft", behind_base: 2, checks: { state: "failing", passed: 0, failed: 1, pending: 0, items: [] } });
  assert.deepEqual(secondaryActions(readyBehind, deriveHeadline(readyBehind, files(0))), ["ready_for_review", "update_branch"]);
  const unpushed = withPr({ state: "draft" });
  assert.deepEqual(secondaryActions(unpushed, deriveHeadline(unpushed, files(1))), []);
  const merged = withPr({ state: "merged" });
  assert.deepEqual(secondaryActions(merged, deriveHeadline(merged, files(0))), []);
});

test("the chip says where things stand in a few words", () => {
  assert.equal(chipLabel(delivery({ state: "none", pull_request: null }), files(3)).label, "3 unpublished files");
  assert.equal(chipLabel(withPr({ checks: { state: "pending", passed: 0, failed: 0, pending: 1, items: [] } }), files(0)).label, "PR #12 · Checks running");
  assert.equal(chipLabel(withPr({}), files(2)).label, "PR #12 · 2 unpushed files");
  assert.equal(chipLabel(withPr({ state: "merged" }), files(0)).label, "PR #12 · Merged");
});

test("every capability and state that blocks an action says why", () => {
  const changes = (workspace = "ready") => ({ workspace, baseline: "base", files: [], additions: 0, deletions: 0, truncated: false, diff_version: "x" });
  const reason = (action, view, workspace) => { const gate = gateAction(action, view, changes(workspace)); return gate.enabled ? null : gate.reason; };
  assert.equal(reason("push_update", delivery({ capabilities: { writes_enabled: false, can_write: true, checks_readable: true } })), "Publishing is turned off on this server");
  assert.equal(reason("create_pull_request", delivery({ capabilities: { writes_enabled: true, can_write: false, checks_readable: true } })), "You only have read access to o/r");
  assert.equal(reason("push_update", delivery({ busy: true })), "The agent is working…");
  assert.equal(reason("discard", delivery({ busy: true })), "The agent is working…");
  assert.equal(reason("merge", delivery({ pending_operation: { kind: "merge", started_at: "t" } })), "Confirming with GitHub…");
  assert.equal(reason("push_update", delivery(), "expired"), "The sandbox expired, so there is nothing left to publish");
  assert.equal(reason("merge", withPr({ merge_methods: [] })), "This repository allows no merge method");
  assert.equal(reason("push_update", delivery({ sync_error: "github_reauthorization_required" })), "Reconnect GitHub to continue");
  // Not blocked: merging while the agent works, and unknown write access (the server decides).
  assert.equal(reason("merge", delivery({ busy: true })), null);
  assert.equal(reason("push_update", delivery({ capabilities: { writes_enabled: true, can_write: null, checks_readable: null } })), null);
  // Asking the agent and starting over need no GitHub write access, only an idle agent.
  assert.equal(reason("fix_checks", delivery({ capabilities: { writes_enabled: false, can_write: false, checks_readable: true } })), null);
  assert.equal(reason("fix_checks", delivery({ busy: true })), "The agent is working…");
  assert.equal(reason("continue", delivery({ busy: true })), "The agent is working…");
  // A published branch can still get its pull request after the sandbox is gone; a first publish cannot.
  assert.equal(reason("create_pull_request", delivery({ state: "branch", pull_request: null }), "expired"), null);
  assert.equal(reason("create_pull_request", delivery({ state: "none", pull_request: null }), "expired"), "The sandbox expired, so there is nothing left to publish");
});

const changed = (files) => ({ workspace: "ready", baseline: "base", files, additions: 1, deletions: 0, truncated: false, diff_version: "x" });
const file = (path, blocked = null) => ({ path, status: "added", additions: 1, deletions: 0, binary: false, truncated: false, patch: "", blocked });

test("blocked files stop publishing (never discarding), and the note and the button say the same thing", () => {
  const view = delivery({ state: "none", pull_request: null });
  const found = changed([file("src/a.ts"), file(".env", "secret")]);
  const publish = gateAction("create_pull_request", view, found);
  assert.deepEqual(publish, { enabled: false, reason: "1 changed file can't be published. Discard it to continue" });
  assert.equal(gateAction("push_update", withPr({}), found).enabled, false);
  assert.equal(gateAction("discard", view, found).enabled, true);
  assert.equal(gateAction("create_pull_request", view, changed([file("src/a.ts")])).enabled, true);
  assert.equal(deliveryNotices(view, found).find((notice) => notice.id === "blocked").text, publish.reason);
  assert.match(blockedReason(2), /^2 changed files can't be published\. Discard them to continue$/);
  assert.deepEqual(blockedFiles(found).map((entry) => entry.path), [".env"]);
});

test("discarding a file needs no GitHub write access", () => {
  const readOnly = delivery({ capabilities: { writes_enabled: false, can_write: false, checks_readable: true } });
  assert.equal(gateAction("discard", readOnly, changed([file(".env", "secret")])).enabled, true);
});

test("the card and chip use the delivery's own summary until the detail is open", () => {
  const view = delivery({ unpublished: { files: 3, additions: 40, deletions: 5, updated_at: "t" } });
  const detail = changed([file("a"), file("b")]);
  assert.deepEqual(unpublishedSummary(view, null, false), { files: 3, additions: 40, deletions: 5 });
  assert.deepEqual(unpublishedSummary(view, detail, false), { files: 3, additions: 40, deletions: 5 });
  assert.deepEqual(unpublishedSummary(view, detail, true), { files: 2, additions: 1, deletions: 0 });
  assert.equal(unpublishedSummary(delivery({ unpublished: null }), null, false), null);
  assert.deepEqual(unpublishedSummary(delivery({ unpublished: null }), detail, false), { files: 2, additions: 1, deletions: 0 });
});

test("notices explain standing conditions in the contract's words", () => {
  const changes = { workspace: "expired", baseline: "base", files: [], additions: 0, deletions: 0, truncated: false, diff_version: "x" };
  const texts = (view, found = null) => deliveryNotices(view, found).map((notice) => notice.text);
  assert.deepEqual(texts(delivery({ capabilities: { writes_enabled: false, can_write: null, checks_readable: true } })), ["Publishing is turned off on this server"]);
  assert.deepEqual(texts(delivery({ capabilities: { writes_enabled: true, can_write: false, checks_readable: true } })), ["You only have read access to o/r"]);
  assert.deepEqual(texts(delivery({ capabilities: { writes_enabled: true, can_write: true, checks_readable: false } })), ["Checks unavailable — grant the App Checks:read"]);
  assert.deepEqual(texts(withPr({ checks: { state: "unavailable", passed: 0, failed: 0, pending: 0, items: [] } })), ["Checks unavailable — grant the App Checks:read"]);
  assert.match(texts(delivery(), changes)[0], /sandbox has expired.*not published are lost/);
  assert.deepEqual(texts(delivery({ busy: true })), ["The agent is working…"]);
  assert.deepEqual(texts(delivery({ pending_operation: { kind: "merge", started_at: "t" } })), ["Confirming with GitHub…"]);
  assert.deepEqual(texts(delivery()), []);
});

test("checks sort failing, then pending, then passed, then neutral, keeping order within a group", () => {
  const item = (name, status) => ({ name, status, url: null, workflow: null });
  const sorted = sortChecks([item("a", "passed"), item("b", "neutral"), item("c", "failed"), item("d", "pending"), item("e", "passed"), item("f", "failed")]);
  assert.deepEqual(sorted.map(({ name }) => name), ["c", "f", "d", "a", "e", "b"]);
});

test("the merge sentence names the method, the pull request and the base", () => {
  assert.equal(mergeSentence("squash", 12, "main"), "Squash and merge #12 into main");
  assert.equal(mergeSentence("merge", 12, "main"), "Merge #12 into main");
  assert.equal(mergeSentence("rebase", 7, "develop"), "Rebase and merge #7 into develop");
});

test("the merge method is the remembered one only if the repository still allows it", () => {
  assert.equal(pickMergeMethod(["squash", "merge", "rebase"], "rebase"), "rebase");
  assert.equal(pickMergeMethod(["merge", "rebase"], "squash"), "merge");
  assert.equal(pickMergeMethod(["rebase"], null), "rebase");
  assert.equal(pickMergeMethod([], "squash"), null);
});

test("the canned prompts tell the agent it cannot push", () => {
  const view = withPr({ number: 9 });
  assert.equal(agentPrompt("fix_checks", view), "Investigate the failing checks on PR #9, fix the failures, run the relevant tests, and tell me when the changes are ready to push.");
  assert.equal(agentPrompt("resolve", view), "The pull request has merge conflicts with main. Look at what changed and resolve it in the workspace, then tell me when the changes are ready to push.");
  assert.equal(agentPrompt("address_feedback", view), "Address the requested changes on PR #9, run the relevant tests, and tell me when the changes are ready to push.");
});

test("links are rendered only when they are plain https", () => {
  assert.equal(safeHref("https://github.com/o/r/pull/1"), "https://github.com/o/r/pull/1");
  for (const bad of ["javascript:alert(1)", "http://github.com/x", "https://user:pw@github.com/x", "", null, undefined, "not a url"]) assert.equal(safeHref(bad), undefined);
});

test("small helpers", () => {
  assert.deepEqual(splitPath("src/lib/a.ts"), { dir: "src/lib/", name: "a.ts" });
  assert.deepEqual(splitPath("README.md"), { dir: "", name: "README.md" });
  const now = Date.parse("2026-01-01T12:00:00Z");
  assert.equal(relativeTime("2026-01-01T11:59:55Z", now), "just now");
  assert.equal(relativeTime("2026-01-01T11:58:00Z", now), "2m ago");
  assert.equal(relativeTime("2026-01-01T09:00:00Z", now), "3h ago");
  assert.equal(relativeTime(null, now), "");
  assert.equal(hashText("a"), hashText("a"));
  assert.notEqual(hashText("a"), hashText("b"));
});

const patch = [
  "diff --git a/src/a.ts b/src/a.ts",
  "index 111..222 100644",
  "--- a/src/a.ts",
  "+++ b/src/a.ts",
  "@@ -1,4 +1,5 @@ function x() {",
  " one",
  "-two",
  "-- three starts with dashes",
  "+two!",
  "+++ looks like a header",
  "+extra",
  " four",
  "\\ No newline at end of file",
  "@@ -20 +21 @@",
  "-old",
  "+new",
  "",
].join("\n");

test("a unified diff is read hunk by hunk with line numbers, and lines that look like headers stay lines", () => {
  const rows = parsePatch(patch);
  assert.deepEqual(rows.map((row) => row.kind), ["hunk", "ctx", "del", "del", "add", "add", "add", "ctx", "note", "hunk", "del", "add"]);
  assert.deepEqual(rows[1], { kind: "ctx", text: "one", oldLine: 1, newLine: 1 });
  assert.deepEqual(rows[2], { kind: "del", text: "two", oldLine: 2, newLine: null });
  assert.equal(rows[3].text, "- three starts with dashes");
  assert.deepEqual(rows[4], { kind: "add", text: "two!", oldLine: null, newLine: 2 });
  assert.equal(rows[5].text, "++ looks like a header");
  assert.deepEqual(rows[7], { kind: "ctx", text: "four", oldLine: 4, newLine: 5 });
  assert.equal(rows[8].text, "No newline at end of file");
  assert.deepEqual(rows[10], { kind: "del", text: "old", oldLine: 20, newLine: null });
  assert.deepEqual(rows[11], { kind: "add", text: "new", oldLine: null, newLine: 21 });
});

test("a patch that is empty, binary or cut short does not break the reader", () => {
  assert.deepEqual(parsePatch(""), []);
  const cut = parsePatch("@@ -1,10 +1,10 @@\n a\n-b\n+c");
  assert.deepEqual(cut.map((row) => row.kind), ["hunk", "ctx", "del", "add"]);
  // A new file and a deleted file have a zero-length side.
  assert.deepEqual(parsePatch("@@ -0,0 +1,2 @@\n+a\n+b").map((row) => row.kind), ["hunk", "add", "add"]);
  assert.deepEqual(parsePatch("@@ -1,2 +0,0 @@\n-a\n-b").map((row) => row.kind), ["hunk", "del", "del"]);
  assert.deepEqual(parsePatch("@@ -1,2 +1,2 @@\r\n a\r\n-b\r\n+c\r\n").map((row) => row.text), ["@@ -1,2 +1,2 @@", "a", "b", "c"]);
});
