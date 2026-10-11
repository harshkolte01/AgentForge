import assert from "node:assert/strict";
import { test } from "node:test";
import {
  classifyFailure, createDetailLoader, detailReducer, diffLineKind, diffTotals, errorText, executionRows, formatDuration, initialDetailState,
  retryableReasons, safeDownloadHref, truncationNotice, unavailableMessage,
} from "../features/sessions/tool-detail-model.ts";
import { ApiError } from "../lib/task-api.ts";

const detail = (patch = {}) => ({
  task_id: "t", tool_call_id: "c", tool: "run_tests", kind: "execution", outcome: "completed", error_code: null, started_at: "2026-01-01T00:00:00Z",
  finished_at: "2026-01-01T00:00:03Z", duration_ms: 3400, execution: { profile_id: "node_test", exit_code: 0, timed_out: false, qualified: true, output_truncated: false, observed_test_count: 12 },
  files: [], stdout: null, stderr: null, diff: null, preview: null, artifacts: [], unavailable_reason: null, ...patch,
});

// Words ---------------------------------------------------------------------------------------------------------------

test("unavailable reasons map to friendly copy; unknown reasons never leak the raw code", () => {
  assert.match(unavailableMessage("evidence_unavailable"), /no longer available/);
  assert.match(unavailableMessage("artifact_missing"), /saved file/);
  assert.match(unavailableMessage("no_output_recorded"), /No output/);
  assert.equal(unavailableMessage("something_new"), "Some details aren't available.");
  assert.equal(unavailableMessage(null), null);
  assert.ok(retryableReasons.has("artifact_unavailable"));
  assert.ok(!retryableReasons.has("evidence_unavailable"));
});

test("error text is a sentence for known codes, the bare code for well-formed ones, nothing for anything else", () => {
  assert.equal(errorText("TOOL_TIMEOUT"), "The action timed out.");
  assert.equal(errorText("ODD_NEW_CODE"), "Error code: ODD_NEW_CODE");
  assert.equal(errorText("lower case"), null);
  assert.equal(errorText("<b>x</b>"), null);
  assert.equal(errorText(null), null);
});

test("durations read naturally", () => {
  assert.equal(formatDuration(420), "420 ms");
  assert.equal(formatDuration(1200), "1.2 s");
  assert.equal(formatDuration(3000), "3 s");
  assert.equal(formatDuration(42_000), "42 s");
  assert.equal(formatDuration(125_000), "2 min 05 s");
  assert.equal(formatDuration(null), null);
  assert.equal(formatDuration(-5), null);
});

test("a clipped preview says so, with sizes when they are known", () => {
  assert.equal(truncationNotice({ text: "abc", truncated: false, bytes: 3 }), null);
  assert.equal(truncationNotice({ text: "x".repeat(2048), truncated: true, bytes: 40_000 }), "Truncated: showing the first 2 KB of 39 KB.");
  assert.equal(truncationNotice({ text: "abc", truncated: true, bytes: null }), "Truncated: showing only the first part.");
});

// Execution summary ------------------------------------------------------------------------------------------------------

test("the execution summary leads with an outcome derived from evidence, not from the tool having returned", () => {
  const passed = executionRows(detail());
  assert.deepEqual(passed[0], { label: "Outcome", value: "Tests passed" });
  assert.deepEqual(passed.slice(1), [
    { label: "Profile", value: "node_test" }, { label: "Exit code", value: "0" }, { label: "Timed out", value: "No" },
    { label: "Qualified result", value: "Yes" }, { label: "Tests observed", value: "12" }, { label: "Duration", value: "3.4 s" },
  ]);
  const failed = executionRows(detail({ execution: { ...detail().execution, exit_code: 1 } }));
  assert.equal(failed[0].value, "Tests failed");
  assert.equal(executionRows(detail({ execution: { ...detail().execution, timed_out: true } }))[0].value, "Tests timed out");
  assert.equal(executionRows(detail({ execution: { ...detail().execution, qualified: false } }))[0].value, "Ran tests (result not qualified)");
});

test("outcomes without evidence and absent facts are handled without inventing anything", () => {
  const rows = executionRows(detail({ outcome: "unknown", execution: null, duration_ms: null }));
  assert.deepEqual(rows, [{ label: "Outcome", value: "Running tests may not have finished" }]);
  assert.equal(executionRows(detail({ outcome: "running", execution: null }))[0].value, "Running");
  assert.equal(executionRows(detail({ outcome: "cancelled", execution: null }))[0].value, "Stopped running tests");
  const cut = executionRows(detail({ execution: { ...detail().execution, output_truncated: true } }));
  assert.ok(cut.some((row) => row.label === "Output"));
});

test("a build profile reads as a build", () => {
  const rows = executionRows(detail({ tool: "run_sandbox_command", execution: { ...detail().execution, profile_id: "node_build", exit_code: 2 } }));
  assert.equal(rows[0].value, "Build failed");
});

// Diff ------------------------------------------------------------------------------------------------------------------

test("changed-file totals and diff line kinds", () => {
  assert.equal(diffTotals([]), "");
  assert.equal(diffTotals([{ path: "a", additions: 3, deletions: 1 }, { path: "b", additions: 10, deletions: null }]), "2 files changed, +13 −1");
  assert.equal(diffTotals([{ path: "a", additions: null, deletions: null }]), "1 file changed");
  assert.deepEqual(["+added", "-removed", "@@ -1 +1 @@", " context", "+++ b/x", "--- a/x", ""].map(diffLineKind), ["add", "del", "hunk", "plain", "plain", "plain", "plain"]);
});

test("only this task's own artifact route becomes a download link", () => {
  const ok = "/api/v1/tasks/task-1/artifacts/abc-123";
  assert.equal(safeDownloadHref("task-1", ok), ok);
  assert.equal(safeDownloadHref("task-2", ok), null, "another task's route");
  assert.equal(safeDownloadHref("task-1", "https://evil.example/api/v1/tasks/task-1/artifacts/abc"), null);
  assert.equal(safeDownloadHref("task-1", "/api/v1/tasks/task-1/artifacts/../../x"), null);
  assert.equal(safeDownloadHref("task-1", "/api/v1/tasks/task-1/artifacts/abc?x=1"), null);
  assert.equal(safeDownloadHref("task-1", "javascript:alert(1)"), null);
});

// State machine ------------------------------------------------------------------------------------------------------------

test("a failed request is sorted into nothing-to-show, session gone, or worth another try", () => {
  assert.equal(classifyFailure(new ApiError(404, "There are no details to show for this action.", "tool_detail_unavailable")).kind, "unavailable");
  assert.equal(classifyFailure(new ApiError(401, "Your session has expired. Sign in again to continue.", "session_required")).kind, "session");
  assert.equal(classifyFailure(new ApiError(500, "The request could not be completed (HTTP 500). Try again.")).kind, "error");
  const network = classifyFailure(new TypeError("Failed to fetch"));
  assert.equal(network.kind, "error");
  assert.doesNotMatch(network.message, /Failed to fetch/, "raw browser errors are not shown");
});

test("the panel opens loading (or ready from the cache) and moves through loaded / failed / retry", () => {
  assert.deepEqual(initialDetailState(), { status: "loading", attempt: 1 });
  const cached = detail();
  assert.deepEqual(initialDetailState(cached), { status: "ready", detail: cached });

  let state = initialDetailState();
  state = detailReducer(state, { type: "failed", attempt: 1, failure: { kind: "error", message: "Try again." } });
  assert.deepEqual(state, { status: "error", message: "Try again.", attempt: 1 });
  state = detailReducer(state, { type: "retry" });
  assert.deepEqual(state, { status: "loading", attempt: 2 });
  state = detailReducer(state, { type: "loaded", attempt: 2, detail: cached });
  assert.equal(state.status, "ready");
});

test("a result from an earlier attempt, or arriving after the state moved on, changes nothing", () => {
  const retrying = { status: "loading", attempt: 2 };
  assert.equal(detailReducer(retrying, { type: "loaded", attempt: 1, detail: detail() }), retrying);
  assert.equal(detailReducer(retrying, { type: "failed", attempt: 1, failure: { kind: "error", message: "x" } }), retrying);
  const ready = { status: "ready", detail: detail() };
  assert.equal(detailReducer(ready, { type: "failed", attempt: 1, failure: { kind: "error", message: "x" } }), ready);
  assert.equal(detailReducer(retrying, { type: "retry" }), retrying, "retry while loading is a no-op");
});

test("an unavailable detail shows its message and can be asked again", () => {
  let state = detailReducer(initialDetailState(), { type: "failed", attempt: 1, failure: { kind: "unavailable", message: "Nothing here." } });
  assert.deepEqual(state, { status: "unavailable", message: "Nothing here." });
  state = detailReducer(state, { type: "retry" });
  assert.deepEqual(state, { status: "loading", attempt: 1 });
});

// Loader and cache -------------------------------------------------------------------------------------------------------------

test("the loader fetches once per call and remembers finished answers for the page session", async () => {
  const calls = [];
  const loader = createDetailLoader(async (taskId, toolCallId) => { calls.push([taskId, toolCallId]); return detail({ tool_call_id: toolCallId }); });
  assert.equal(loader.peek("t", "c1"), undefined);
  const first = await loader.load("t", "c1");
  assert.equal(loader.peek("t", "c1"), first);
  assert.deepEqual(calls, [["t", "c1"]]);
  assert.equal(loader.peek("t", "c2"), undefined, "other calls are not fetched or cached");
  assert.equal(loader.peek("other", "c1"), undefined, "the cache is per task");
});

test("running calls and previews that could not be read are not cached", async () => {
  let next = detail({ outcome: "running" });
  const loader = createDetailLoader(async () => next);
  await loader.load("t", "c");
  assert.equal(loader.peek("t", "c"), undefined);
  next = detail({ unavailable_reason: "artifact_unavailable" });
  await loader.load("t", "c");
  assert.equal(loader.peek("t", "c"), undefined);
  next = detail();
  await loader.load("t", "c");
  assert.ok(loader.peek("t", "c"));
  loader.clear();
  assert.equal(loader.peek("t", "c"), undefined);
});

test("the loader passes the abort signal through, and failures are not cached", async () => {
  const controller = new AbortController();
  let seen;
  const loader = createDetailLoader(async (_t, _c, init) => { seen = init.signal; throw new ApiError(500, "no"); });
  await assert.rejects(loader.load("t", "c", controller.signal));
  assert.equal(seen, controller.signal);
  assert.equal(loader.peek("t", "c"), undefined);
});

test("the cache is bounded and evicts the oldest call", async () => {
  const loader = createDetailLoader(async (_t, id) => detail({ tool_call_id: id }), 2);
  await loader.load("t", "a"); await loader.load("t", "b"); await loader.load("t", "c");
  assert.equal(loader.peek("t", "a"), undefined);
  assert.ok(loader.peek("t", "b") && loader.peek("t", "c"));
});
