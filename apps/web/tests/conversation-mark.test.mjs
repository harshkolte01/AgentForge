import assert from "node:assert/strict";
import { test } from "node:test";
import { conversationMark, pullRequestKey } from "../lib/conversation-mark.ts";

const chat = (status = "idle", delivery = null, active_task_id = null) => ({ status, active_task_id, delivery });
const pr = (over = {}) => ({ state: "pull_request", pr_state: "open", checks: "passing", review: "none", mergeable: "mergeable", unpublished: false, ...over });

test("work that is going on or needs you comes before anything about GitHub", () => {
  assert.equal(conversationMark(chat("running", pr({ pr_state: "merged" }), "t1"), false).key, "running");
  assert.equal(conversationMark(chat("waiting_approval", pr(), "t1"), true).key, "waiting_approval");
  assert.equal(conversationMark(chat("failed", pr()), false).key, "failed");
  assert.equal(conversationMark(chat("unread", pr()), false).key, "unread");
});

test("a failure or unread reply you are looking at yields to the pull request", () => {
  assert.equal(conversationMark(chat("unread", pr({ checks: "failing" })), true).key, "pr_failing");
});

test("a pull request shows its state, worst news first", () => {
  const key = (over) => pullRequestKey(pr(over));
  assert.equal(key({ pr_state: "merged", checks: "failing" }), "pr_merged");
  assert.equal(key({ pr_state: "closed" }), "pr_closed");
  assert.equal(key({ mergeable: "conflicting", checks: "failing" }), "pr_conflicts");
  assert.equal(key({ review: "changes_requested", checks: "failing" }), "pr_changes");
  assert.equal(key({ checks: "failing" }), "pr_failing");
  assert.equal(key({ checks: "pending" }), "pr_running");
  assert.equal(key({ pr_state: "draft" }), "pr_draft");
  assert.equal(key({ review: "approved" }), "pr_approved");
  assert.equal(key({}), "pr_open");
  assert.equal(key({ checks: "unavailable" }), "pr_open", "unreadable checks are not bad news");
});

test("a healthy open pull request is the quiet resting state; everything else is worth a tooltip", () => {
  assert.equal(conversationMark(chat("idle", pr()), false).notable, false);
  for (const over of [{ pr_state: "merged" }, { checks: "failing" }, { checks: "pending" }, { pr_state: "draft" }, { review: "approved" }]) {
    assert.equal(conversationMark(chat("idle", pr(over)), false).notable, true, JSON.stringify(over));
  }
});

test("without a pull request: unpushed work, a published branch, or nothing", () => {
  assert.equal(conversationMark(chat("idle", { state: "none", unpublished: true }), false).key, "unpublished");
  assert.equal(conversationMark(chat("idle", { state: "branch", unpublished: false }), false).key, "branch");
  assert.equal(conversationMark(chat("idle", null), false).key, "idle");
  assert.equal(conversationMark(chat("idle", undefined), false).key, "idle", "an older server sends no delivery");
  assert.equal(conversationMark(chat("idle", { state: "none" }), false).key, "idle");
});

test("unpushed work on a conversation with a pull request is the pull request's story, not a second mark", () => {
  assert.equal(conversationMark(chat("idle", pr({ unpublished: true })), false).key, "pr_open");
});
