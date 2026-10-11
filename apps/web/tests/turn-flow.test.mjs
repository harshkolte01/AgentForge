import assert from "node:assert/strict";
import { test } from "node:test";
import {
  announcementForReceipt, announcements, canType, createLatest, createScope, fingerprintOf, isStopping, navigationAfterSend,
  nextSubmission, pendingFor, receiptEffects, runBlocksSending, sendBlock, sessionAfterReceipt, turnPhase, turnRowKey, waitForSavedAnswer,
} from "../features/sessions/turn-flow.ts";

const TERMINAL = new Set(["COMPLETED", "PARTIAL", "FAILED", "CANCELLED"]);
const isTerminal = (status) => TERMINAL.has(status);
const input = (goal = "hello", extra = {}) => ({ routing: "auto", goal, repository: null, allowed_web_hosts: [], attachments: [], ...extra });
const user = (sequence = 5) => ({ id: `u${sequence}`, session_id: "s", sequence, role: "user", content: "hello", task_id: null, created_at: "2026-01-01T00:00:00Z" });
const assistant = (sequence = 6, task_id = "t1") => ({ id: `a${sequence}`, session_id: "s", sequence, role: "assistant", content: "hi", task_id, created_at: "2026-01-01T00:00:00Z" });
const receipt = (extra = {}) => ({ session_id: "s", user_message: user(), task: { id: "t1", status: "RUNNING" }, assistant_message: null, replayed: false, state: "running", ...extra });
const session = (extra = {}) => ({ id: "s", last_sequence: 4, active_task_id: null, ...extra });

test("an unchanged retry reuses the idempotency key; a changed message gets a new one", () => {
  let n = 0;
  const makeKey = () => `key-${++n}`;
  const first = nextSubmission(null, input("hello"), makeKey);
  assert.equal(first.key, "key-1");
  const retry = nextSubmission(first, input("hello"), makeKey);
  assert.equal(retry, first, "the same submission object");
  assert.equal(n, 1, "no new key was made");
  const edited = nextSubmission(first, input("hello again"), makeKey);
  assert.equal(edited.key, "key-2");
  assert.equal(nextSubmission(edited, input("hello again", { attachments: [{ name: "a.txt", content: "x" }] }), makeKey).key, "key-3", "attachments are part of the message");
  assert.equal(nextSubmission(edited, input("hello again", { repository: { repo_id: "r", ref: "main" } }), makeKey).key, "key-4", "so is the repository");
  assert.equal(fingerprintOf(input("a")), fingerprintOf(input("a")));
  assert.notEqual(fingerprintOf(input("a")), fingerprintOf(input("b")));
});

test("typing stays available during a run; only sending is blocked", () => {
  const idle = { disabled: false, archived: false, busy: false, runBlocks: false, configured: true, goal: "hi" };
  assert.equal(sendBlock(idle), null);
  assert.equal(sendBlock({ ...idle, runBlocks: true }), "running");
  assert.equal(runBlocksSending(true, false), true, "a run blocks sending when the server does not queue");
  assert.equal(runBlocksSending(true, true), false, "and does not when it queues");
  assert.equal(runBlocksSending(true, undefined), false, "an older server that does not say is not assumed to block");
  assert.equal(runBlocksSending(false, false), false);
  assert.equal(canType({ disabled: false, archived: false, busy: false }), true, "a run does not lock the field");
  assert.equal(canType({ disabled: false, archived: false, busy: true }), false, "a send in flight does");
  assert.equal(canType({ disabled: true, archived: false, busy: false }), false);
  assert.equal(canType({ disabled: false, archived: true, busy: false }), false);
  assert.equal(sendBlock({ ...idle, goal: "   " }), "empty");
  assert.equal(sendBlock({ ...idle, configured: false }), "unconfigured");
  assert.equal(sendBlock({ ...idle, busy: true }), "busy");
  assert.equal(sendBlock({ ...idle, archived: true }), "archived");
  assert.equal(sendBlock({ ...idle, disabled: true }), "disabled");
});

test("the stop request belongs to its run, so the next run's Stop works again", () => {
  assert.equal(isStopping(null, "a", true), false);
  assert.equal(isStopping("a", "a", true), true);
  assert.equal(isStopping("a", "b", true), false, "a new run");
  assert.equal(isStopping("a", "a", false), false, "the run ended");
  assert.equal(isStopping("current", null, true), true);
});

test("a receipt for a running task reconciles the conversation without any further request", () => {
  const effects = receiptEffects(receipt(), isTerminal);
  assert.deepEqual(effects.messages.map((entry) => entry.id), ["u5"]);
  assert.equal(effects.activeTaskId, "t1");
  assert.equal(effects.queued, false);
  const patched = sessionAfterReceipt(session(), effects);
  assert.equal(patched.active_task_id, "t1");
  assert.equal(patched.last_sequence, 5);
  assert.equal(announcementForReceipt(effects), announcements.sentWorking);
});

test("a receipt that already carries the answer (an instant reply or a replay) starts no run", () => {
  const instant = receiptEffects(receipt({ task: null, assistant_message: assistant(6, null) }), isTerminal);
  assert.deepEqual(instant.messages.map((entry) => entry.id), ["u5", "a6"]);
  assert.equal(instant.activeTaskId, null);
  assert.equal(sessionAfterReceipt(session(), instant).last_sequence, 6);
  assert.equal(announcementForReceipt(instant), announcements.sent);
  const done = receiptEffects(receipt({ task: { id: "t1", status: "COMPLETED" }, assistant_message: assistant(), replayed: true }), isTerminal);
  assert.equal(done.activeTaskId, null);
  assert.equal(done.replayed, true);
});

test("a replayed receipt never claims the conversation's active task", () => {
  const replay = receiptEffects(receipt({ replayed: true }), isTerminal);
  const patched = sessionAfterReceipt(session({ active_task_id: "t9", last_sequence: 9 }), replay);
  assert.equal(patched.active_task_id, "t9");
  assert.equal(patched.last_sequence, 9);
});

test("a queued receipt shows the message but starts no run", () => {
  const queued = receiptEffects(receipt({ task: null, state: "queued" }), isTerminal);
  assert.equal(queued.queued, true);
  assert.equal(queued.activeTaskId, null);
  assert.equal(announcementForReceipt(queued), announcements.queued);
});

test("a receipt that changes nothing returns the very same session", () => {
  const same = session({ last_sequence: 9, active_task_id: "t1" });
  const effects = receiptEffects(receipt(), isTerminal);
  assert.equal(sessionAfterReceipt(same, effects), same);
});

test("after a send, move to the new conversation only if the reader has not gone elsewhere", () => {
  assert.equal(navigationAfterSend({ routeAtSend: null, routeNow: null, resultSessionId: "s" }), "navigate", "first message of a new conversation");
  assert.equal(navigationAfterSend({ routeAtSend: "s", routeNow: "s", resultSessionId: "s" }), "stay");
  assert.equal(navigationAfterSend({ routeAtSend: null, routeNow: "other", resultSessionId: "s" }), "stay", "they opened another conversation meanwhile");
  assert.equal(navigationAfterSend({ routeAtSend: "a", routeNow: "b", resultSessionId: "a" }), "stay");
});

test("the pending bubble belongs to one conversation and keeps one row identity", () => {
  const pending = { key: "abc", sessionId: "s", goal: "hi", attachments: [] };
  assert.equal(pendingFor(pending, "s"), pending);
  assert.equal(pendingFor(pending, "other"), null, "a late send never paints into another conversation");
  assert.equal(pendingFor(null, "s"), null);
  assert.equal(turnRowKey("abc"), "turn:abc");
});

test("scope: a reset makes every outstanding ticket stale and aborts its request", () => {
  const scope = createScope();
  const a = scope.ticket();
  const b = scope.ticket();
  assert.equal(a.isCurrent() && b.isCurrent(), true, "tickets of one scope coexist");
  scope.reset();
  assert.equal(a.isCurrent(), false);
  assert.equal(b.signal.aborted, true);
  assert.equal(scope.ticket().isCurrent(), true, "new tickets work after a reset");
});

test("latest: beginning a request cancels the previous one; a scope reset cancels both", () => {
  const scope = createScope();
  const latest = createLatest(scope);
  const first = latest.begin();
  const second = latest.begin();
  assert.equal(first.isCurrent(), false);
  assert.equal(first.signal.aborted, true);
  assert.equal(second.isCurrent(), true);
  scope.reset();
  assert.equal(second.isCurrent(), false);
  assert.equal(second.signal.aborted, true);
  const standalone = createLatest().begin();
  assert.equal(standalone.isCurrent(), true);
});

test("a delayed response from a conversation that was left cannot apply", async () => {
  const scope = createScope();
  const ticket = scope.ticket();
  let applied = null;
  const late = new Promise((resolve) => setTimeout(() => resolve("old conversation data"), 5)).then((data) => { if (ticket.isCurrent()) applied = data; });
  scope.reset(); // the reader navigated away
  await late;
  assert.equal(applied, null);
});

const sleeper = () => { const slept = []; return { slept, sleep: async (ms) => { slept.push(ms); } }; };

test("saved answer: found on the first read", async () => {
  const { slept, sleep } = sleeper();
  const result = await waitForSavedAnswer({ taskId: "t1", read: async () => assistant(), sleep, isCurrent: () => true });
  assert.equal(result.status, "found");
  assert.equal(result.message.id, "a6");
  assert.deepEqual(slept, []);
});

test("saved answer: a missing answer and failed reads are retried with backoff until it appears", async () => {
  const { slept, sleep } = sleeper();
  let reads = 0;
  const result = await waitForSavedAnswer({
    taskId: "t1", sleep, isCurrent: () => true,
    read: async () => { reads++; if (reads === 1) throw new Error("network"); if (reads === 2) return undefined; return assistant(); },
  });
  assert.equal(result.status, "found");
  assert.equal(reads, 3);
  assert.deepEqual(slept, [400, 800]);
});

test("saved answer: it gives up visibly instead of declaring the turn done", async () => {
  const { slept, sleep } = sleeper();
  const failure = new Error("network");
  const result = await waitForSavedAnswer({ taskId: "t1", sleep, isCurrent: () => true, read: async () => { throw failure; } });
  assert.equal(result.status, "gave_up");
  assert.equal(result.error, failure);
  assert.deepEqual(slept, [400, 800, 1600, 3200, 6000], "bounded backoff");
  const missing = await waitForSavedAnswer({ taskId: "t1", sleep, isCurrent: () => true, read: async () => undefined });
  assert.equal(missing.status, "gave_up");
  assert.equal(missing.error, null, "no error, just not there yet");
});

test("saved answer: a conversation change in the middle stops it without touching anything", async () => {
  const { sleep } = sleeper();
  let current = true;
  let reads = 0;
  const result = await waitForSavedAnswer({ taskId: "t1", sleep, isCurrent: () => current, read: async () => { reads++; current = false; return assistant(); } });
  assert.equal(result.status, "stale");
  assert.equal(reads, 1);
  const before = await waitForSavedAnswer({ taskId: "t1", sleep, isCurrent: () => false, read: async () => assistant() });
  assert.equal(before.status, "stale");
});

test("handoff happens in one step: only when the activity is complete AND the saved answer is in the records", () => {
  const none = new Set();
  const phase = (extra) => turnPhase({ tracked: "t1", saved: none, terminal: none, abandoned: none, ...extra });
  assert.deepEqual(phase({}), { show: true, savedReady: false });
  assert.deepEqual(phase({ terminal: new Set(["t1"]) }), { show: true, savedReady: false }, "events complete, answer not yet saved: still finishing");
  assert.deepEqual(phase({ saved: new Set(["t1"]) }), { show: true, savedReady: true }, "answer saved before the events are complete: keep the live activity");
  assert.deepEqual(phase({ saved: new Set(["t1"]), terminal: new Set(["t1"]) }), { show: false, savedReady: true });
  assert.deepEqual(phase({ abandoned: new Set(["t1"]) }), { show: false, savedReady: false }, "a live view that cannot finish does not hide the saved message");
  assert.deepEqual(turnPhase({ tracked: null, saved: none, terminal: none, abandoned: none }), { show: false, savedReady: false });
  assert.deepEqual(phase({ saved: new Set(["other"]), terminal: new Set(["other"]) }), { show: true, savedReady: false }, "another task's answer is not this one's");
});
