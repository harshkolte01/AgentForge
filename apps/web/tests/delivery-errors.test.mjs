import { test } from "node:test";
import assert from "node:assert/strict";
import { errorPaths as conflictPaths, deliveryMessage, errorCode, needsReconnect, reuseKey, syncErrorMessage } from "../lib/delivery-errors.ts";
import { ApiError } from "../lib/task-api.ts";

const codes = [
  "agent_running", "nothing_to_publish", "delivery_workspace_expired", "delivery_no_repository", "github_branch_moved", "github_path_conflict:a.ts,b.ts",
  "github_base_branch_changed", "github_publish_branch_exists", "github_not_mergeable", "github_draft_pull_request", "github_write_permission_required",
  "github_writes_disabled", "github_reauthorization_required", "github_rate_limited", "github_unavailable", "delivery_operation_in_progress", "delivery_outcome_unknown",
  "delivery_blocked_paths:.env,id_rsa", "github_branch_missing", "github_change_too_large", "delivery_not_published", "github_not_connected", "delivery_sandbox_not_configured",
  "delivery_message_required", "delivery_branch_invalid", "delivery_workspace_unavailable", "github_unsupported_change", "github_unsafe_change_path",
  "delivery_unpublished_changes", "delivery_reset_not_allowed", "delivery_workspace_changed",
];

test("every code in the contract has a sentence of its own", () => {
  const seen = new Set();
  for (const code of codes) {
    const text = deliveryMessage(new ApiError(409, "generic fallback", code));
    assert.notEqual(text, "generic fallback", code);
    assert.ok(text.length > 20 && /[.]$/.test(text), `${code}: ${text}`);
    seen.add(text);
  }
  assert.equal(seen.size, codes.length, "no two codes share a sentence");
});

test("a path conflict names the files, and only some of them when there are many", () => {
  const few = new ApiError(409, "x", "github_path_conflict:src/a.ts,src/b.ts");
  assert.deepEqual(conflictPaths(few), ["src/a.ts", "src/b.ts"]);
  assert.equal(errorCode(few), "github_path_conflict");
  assert.match(deliveryMessage(few), /^src\/a\.ts, src\/b\.ts changed on GitHub since the last push/);
  const many = new ApiError(409, "x", "github_path_conflict:a,b,c,d,e");
  assert.match(deliveryMessage(many), /^a, b, c and 2 more changed on GitHub/);
  assert.deepEqual(conflictPaths(new ApiError(409, "x", "agent_running")), []);
});

test("blocked paths are named, with the secret never described beyond its path", () => {
  const blocked = new ApiError(409, "x", "delivery_blocked_paths:.env,keys/id_rsa");
  assert.deepEqual(conflictPaths(blocked), [".env", "keys/id_rsa"]);
  assert.equal(errorCode(blocked), "delivery_blocked_paths");
  assert.match(deliveryMessage(blocked), /^\.env, keys\/id_rsa can't be published .*Discard them and try again\.$/);
  assert.match(deliveryMessage(new ApiError(409, "x", "delivery_blocked_paths:.env")), /^\.env can't be published .*Discard it and try again\.$/);
});

test("anything else falls back to the message the API layer made", () => {
  assert.equal(deliveryMessage(new ApiError(500, "The request could not be completed (HTTP 500). Try again.")), "The request could not be completed (HTTP 500). Try again.");
  assert.equal(deliveryMessage(new ApiError(409, "Restore this conversation.", "session_archived")), "Restore this conversation.");
  assert.match(deliveryMessage(new TypeError("fetch failed")), /fetch failed/);
});

test("a retry reuses its idempotency key only when the first attempt may have been acted on", () => {
  assert.equal(reuseKey(new TypeError("network down")), true);
  assert.equal(reuseKey(new ApiError(502, "x", "delivery_outcome_unknown")), true);
  assert.equal(reuseKey(new ApiError(503, "x", "github_unavailable")), true);
  assert.equal(reuseKey(new ApiError(500, "x")), true);
  assert.equal(reuseKey(new ApiError(409, "x", "agent_running")), false);
  assert.equal(reuseKey(new ApiError(403, "x", "github_writes_disabled")), false);
  assert.equal(reuseKey(new ApiError(401, "x", "github_reauthorization_required")), false);
});

test("sync errors and reconnecting", () => {
  for (const code of ["github_unavailable", "github_rate_limited", "github_reauthorization_required", "github_access_denied"]) assert.ok(syncErrorMessage(code).length > 20, code);
  assert.equal(needsReconnect("github_reauthorization_required"), true);
  assert.equal(needsReconnect("agent_running"), false);
  assert.equal(needsReconnect(undefined), false);
});
