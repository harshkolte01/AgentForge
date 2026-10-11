import assert from "node:assert/strict";
import { test } from "node:test";
import { hasUnseenOutcome, isLive, sidebarStatus, statusAnnouncement, statusLabels } from "../lib/conversation-status.ts";

const chat = (status, active_task_id = null) => ({ status, active_task_id });

test("work in progress shows, even for the conversation you are looking at", () => {
  for (const viewing of [true, false]) {
    assert.equal(sidebarStatus(chat("running", "t1"), viewing), "running");
    assert.equal(sidebarStatus(chat("waiting_approval", "t1"), viewing), "waiting_approval");
  }
});

test("a failure or a reply you have not seen shows only for conversations you are not looking at", () => {
  assert.equal(sidebarStatus(chat("failed"), false), "failed");
  assert.equal(sidebarStatus(chat("unread"), false), "unread");
  assert.equal(sidebarStatus(chat("failed"), true), null, "you are looking at it: it has just been seen");
  assert.equal(sidebarStatus(chat("unread"), true), null);
});

test("an idle conversation shows nothing", () => {
  assert.equal(sidebarStatus(chat("idle"), false), null);
  assert.equal(sidebarStatus(chat("idle"), true), null);
});

test("an older server without a status still shows its active task as running, and nothing else", () => {
  assert.equal(sidebarStatus({ active_task_id: "t1" }, false), "running");
  assert.equal(sidebarStatus({ active_task_id: null }, false), null);
});

test("a task the client just started shows as running before the server's status catches up", () => {
  assert.equal(sidebarStatus(chat("idle", "t1"), false), "running");
  assert.equal(sidebarStatus(chat("unread", "t1"), false), "running", "a new message in an unread conversation is work again");
});

test("only the server's unseen outcomes ask to be marked as seen", () => {
  assert.equal(hasUnseenOutcome({ status: "unread" }), true);
  assert.equal(hasUnseenOutcome({ status: "failed" }), true);
  for (const status of ["idle", "running", "waiting_approval", undefined]) assert.equal(hasUnseenOutcome({ status }), false, String(status));
});

test("the list is checked quickly only while something is working or waiting for you", () => {
  assert.equal(isLive(chat("running", "t1")), true);
  assert.equal(isLive(chat("waiting_approval", "t1")), true);
  assert.equal(isLive({ active_task_id: "t1" }), true);
  for (const status of ["idle", "failed", "unread"]) assert.equal(isLive(chat(status)), false, status);
});

test("every status has a label for screen readers and tooltips", () => {
  for (const status of ["running", "waiting_approval", "failed", "unread"]) assert.ok(statusLabels[status].length > 3, status);
});

test("announcements say what happened to a conversation you were not looking at", () => {
  assert.equal(statusAnnouncement("Fix login", "running", "unread"), "Fix login finished.");
  assert.equal(statusAnnouncement("Fix login", "waiting_approval", "unread"), "Fix login finished.");
  assert.equal(statusAnnouncement("Fix login", "running", "failed"), "Fix login failed.");
  assert.equal(statusAnnouncement("Fix login", "running", "waiting_approval"), "Fix login is waiting for your approval.");
  assert.equal(statusAnnouncement("Fix login", null, "unread"), "Fix login has a new reply.");
});

test("nothing is announced for a first sighting, no change, work starting or a status clearing", () => {
  assert.equal(statusAnnouncement("Fix login", undefined, "unread"), null, "first time this conversation is seen");
  assert.equal(statusAnnouncement("Fix login", "unread", "unread"), null);
  assert.equal(statusAnnouncement("Fix login", null, "running"), null);
  assert.equal(statusAnnouncement("Fix login", "unread", null), null);
  assert.equal(statusAnnouncement("Fix login", "failed", null), null);
});

test("a long title is shortened in an announcement", () => {
  const spoken = statusAnnouncement("x".repeat(200), "running", "unread");
  assert.ok(spoken.length < 90 && spoken.includes("…"), spoken);
});
