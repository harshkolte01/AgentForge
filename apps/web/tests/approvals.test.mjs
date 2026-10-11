import assert from "node:assert/strict";
import { test } from "node:test";
import { approvalTitle, detailSize, pendingApprovals } from "../lib/activity/approvals.ts";

const event = (sequence, event_type, payload) => ({ task_id: "t", sequence, event_type, payload, created_at: "" });

test("a call waiting for an answer is pending until it is decided", () => {
  const asked = event(1, "approval.required", { approval_id: "a", tool: "bash", detail: "npm test", state: "PENDING" });
  assert.deepEqual(pendingApprovals([asked]), [{ id: "a", tool: "bash", detail: "npm test", truncated: false, sequence: 1 }]);
  assert.deepEqual(pendingApprovals([asked, event(2, "approval.decided", { approval_id: "a", tool: "bash", state: "APPROVED" })]), []);
  assert.deepEqual(pendingApprovals([asked, event(2, "approval.decided", { approval_id: "a", tool: "bash", state: "DENIED" })]), []);
});

test("a subagent's question says which run asks; the main agent's own does not", () => {
  const [own, sub] = pendingApprovals([
    event(1, "approval.required", { approval_id: "a", tool: "bash", detail: "ls" }),
    event(2, "approval.required", { approval_id: "b", tool: "write", detail: "x.py", agent_run_id: "child-1" }),
    event(3, "approval.required", { approval_id: "c", tool: "bash", detail: "pwd", agent_run_id: 7 }),
  ]);
  assert.equal("agentRunId" in own, false);
  assert.equal(sub.agentRunId, "child-1");
  assert.equal("agentRunId" in pendingApprovals([event(1, "approval.required", { approval_id: "c", tool: "bash", agent_run_id: 7 })])[0], false);
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

test("a detail the server cut short says so; anything but true is not truncated", () => {
  const full = "path: notes.md\n" + "line\n".repeat(400);
  const cut = event(1, "approval.required", { approval_id: "a", tool: "write", detail: full, truncated: true });
  const odd = event(2, "approval.required", { approval_id: "b", tool: "bash", detail: "ls", truncated: "yes" });
  const [first, second] = pendingApprovals([cut, odd]);
  assert.equal(first.detail, full);
  assert.equal(first.truncated, true);
  assert.equal(second.truncated, false);
});

test("a long detail reports its size; a short one does not", () => {
  assert.equal(detailSize("npm test"), null);
  assert.equal(detailSize(""), null);
  assert.equal(detailSize(Array.from({ length: 42 }, () => "x").join("\n")), "42 lines · 83 characters");
  assert.equal(detailSize("a".repeat(3120)), "1 line · 3,120 characters");
});
