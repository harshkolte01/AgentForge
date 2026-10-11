import assert from "node:assert/strict";
import { test } from "node:test";
import { approvalTitle, pendingApprovals } from "../lib/activity/approvals.ts";

const event = (sequence, event_type, payload) => ({ task_id: "t", sequence, event_type, payload, created_at: "" });

test("a call waiting for an answer is pending until it is decided", () => {
  const asked = event(1, "approval.required", { approval_id: "a", tool: "bash", detail: "npm test", state: "PENDING" });
  assert.deepEqual(pendingApprovals([asked]), [{ id: "a", tool: "bash", detail: "npm test", sequence: 1 }]);
  assert.deepEqual(pendingApprovals([asked, event(2, "approval.decided", { approval_id: "a", tool: "bash", state: "APPROVED" })]), []);
  assert.deepEqual(pendingApprovals([asked, event(2, "approval.decided", { approval_id: "a", tool: "bash", state: "DENIED" })]), []);
});

test("several waiting calls come back oldest first, and only the undecided ones", () => {
  const events = [
    event(5, "approval.required", { approval_id: "b", tool: "write", detail: "a.py" }),
    event(3, "approval.required", { approval_id: "a", tool: "bash", detail: "ls" }),
    event(6, "approval.decided", { approval_id: "b", tool: "write", state: "APPROVED" }),
  ];
  assert.deepEqual(pendingApprovals(events).map((item) => item.id), ["a"]);
});

test("events without an approval id, and other kinds of events, are ignored", () => {
  assert.deepEqual(pendingApprovals([event(1, "approval.required", { tool: "bash" }), event(2, "tool.started", { tool: "bash", approval_id: "x" })]), []);
});

test("each tool that can ask has its own question", () => {
  assert.equal(approvalTitle("bash"), "Run this command?");
  assert.equal(approvalTitle("write"), "Write this file?");
  assert.equal(approvalTitle("edit"), "Edit this file?");
  assert.equal(approvalTitle("create_pull_request"), "Open this pull request?");
  assert.equal(approvalTitle("something_else"), "Allow this action?");
});
