import assert from "node:assert/strict";
import { test } from "node:test";
import {
  activityEventTypes, createActivityState, isSettled, reduceActivity, selectActionCount, selectCall, selectCalls, selectCommittedEpoch,
  selectContiguous, selectCurrentCallId, selectEntries, selectHasActivity, selectHasRunning, selectHighest, selectMissingRanges, selectNoteCount,
} from "../lib/activity/reducer.ts";
import { buildActivity } from "../lib/activity.ts";
import { builder, codingRun, researchRun } from "./fixtures/streams.mjs";
import { deepFreeze } from "./helpers/deep-freeze.mjs";

const feed = (events, options, taskId = events[0]?.task_id ?? "task-1") => reduceActivity(createActivityState(taskId, options), { type: "events", events });
const add = (state, ...events) => reduceActivity(state, { type: "events", events: events.flat() });
const shape = (state) => selectEntries(state).map((entry) => entry.kind === "note" ? ["note", entry.text] : entry.kind === "group" ? ["group", entry.label, entry.count] : entry.kind === "call" ? ["call", entry.label, entry.outcome] : [entry.kind, entry.label]);
const modern = (options) => builder({ style: "modern", ...options });
/** Runs `write` against the builder and returns just the events it appended. */
const more = (b, write) => { const from = b.events.length; write(b); return b.events.slice(from); };

// Sequences -------------------------------------------------------------------------------------------------------

test("events are deduplicated by (task, sequence) and ignore other tasks", () => {
  const events = researchRun({ style: "modern" });
  const once = feed(events);
  assert.equal(add(once, events), once, "re-delivering everything changes nothing, not even the object");
  assert.equal(add(once, events.slice(5, 9)), once);
  const withStranger = add(once, [{ ...events[3], task_id: "other-task", sequence: 999 }]);
  assert.equal(withStranger, once);
  assert.equal(selectHighest(once), events.length);
  // The same batch twice in one go.
  assert.deepEqual(shape(feed([...events, ...events])), shape(once));
});

test("order of arrival does not matter: the same events in any order give the same timeline", () => {
  const events = codingRun({ style: "modern" });
  const expected = shape(feed(events));
  const shuffled = [...events].sort((a, b) => ((a.sequence * 7919) % 31) - ((b.sequence * 7919) % 31));
  assert.deepEqual(shape(feed(shuffled)), expected);
  let state = createActivityState("task-1");
  for (const event of [...events].reverse()) state = add(state, event);
  assert.deepEqual(shape(state), expected);
  // And in three uneven batches.
  state = createActivityState("task-1");
  state = add(state, events.slice(10)); state = add(state, events.slice(0, 4)); state = add(state, events.slice(4, 10));
  assert.deepEqual(shape(state), expected);
});

test("contiguous and highest are tracked separately; a gap leaves later events applied and reports the hole", () => {
  const b = modern();
  b.add("task.created"); b.note("first", 1); b.call("web_search", "q"); b.call("fetch_webpage", "a.example.com"); b.add("task.status", {});
  const [e1, e2, e3, e4, e5, e6, e7] = b.events;
  let state = add(createActivityState("task-1"), e1, e2, e4, e5, e7);
  assert.equal(selectContiguous(state), 2);
  assert.equal(selectHighest(state), 7);
  assert.deepEqual(selectMissingRanges(state), [[3, 3], [6, 6]]);
  assert.deepEqual(selectMissingRanges(state, 10), [[3, 3], [6, 6], [8, 10]]);
  // Events beyond the hole are already in the timeline: the finish of a call whose start is missing still shows.
  assert.deepEqual(shape(state), [["note", "first"], ["call", "Searched the web", "completed"], ["call", "Reading a web page", "running"]]);
  assert.equal(selectContiguous(add(state, e3)), 5);
  state = add(state, e3, e6);
  assert.equal(selectContiguous(state), 7);
  assert.deepEqual(selectMissingRanges(state), []);
});

test("a leading hole means nothing is contiguous yet", () => {
  const events = researchRun({ style: "modern" });
  const state = add(createActivityState("task-1"), events.slice(2));
  assert.equal(selectContiguous(state), 0);
  assert.deepEqual(selectMissingRanges(state), [[1, 2]]);
  assert.equal(selectContiguous(add(state, events.slice(0, 2))), events.length);
});

test("filling a hole replays in order: a finish that arrived before its start settles it", () => {
  const b = modern();
  const handle = b.start("web_search", "q");
  b.finish(handle);
  const [start, finish] = b.events;
  let state = add(createActivityState("task-1"), finish);
  assert.deepEqual(shape(state), [["call", "Searched the web", "completed"]]);
  assert.equal(selectCalls(state)[0].startSequence, null);
  state = add(state, start);
  assert.deepEqual(shape(state), [["call", "Searched the web", "completed"]]);
  const call = selectCalls(state)[0];
  assert.equal(call.startSequence, 1);
  assert.equal(call.detail, "“q”");
  assert.equal(call.id, `call:${handle.tool_call_id}`);
  assert.equal(selectActionCount(state), 1);
});

// Calls ------------------------------------------------------------------------------------------------------------

test("repeated tool names and parallel calls settle by the persisted tool-call id", () => {
  const b = modern();
  const a = b.start("fetch_webpage", "a.example.com");
  const c = b.start("fetch_webpage", "b.example.com");
  const d = b.start("fetch_webpage", "c.example.com");
  b.finish(d); b.finish(a, { outcome: "failed" }); b.finish(c);
  const state = feed(b.events);
  assert.deepEqual(selectCalls(state).map((call) => [call.detail, call.outcome, call.id]), [
    ["a.example.com", "failed", `call:${a.tool_call_id}`], ["b.example.com", "completed", `call:${c.tool_call_id}`], ["c.example.com", "completed", `call:${d.tool_call_id}`],
  ]);
  // The same tool again later is a new call, never a reuse of an earlier one.
  const again = add(state, more(b, (next) => next.call("fetch_webpage", "a.example.com")));
  assert.equal(selectCalls(again).length, 4);
  assert.equal(selectActionCount(again), 4);
});

test("a doubled start or finish for one tool-call id changes nothing", () => {
  const b = modern();
  const handle = b.start("web_search", "q");
  b.start("web_search", "q", { toolCallId: handle.tool_call_id });
  b.finish(handle);
  b.finish(handle, { outcome: "failed" });
  const state = feed(b.events);
  assert.deepEqual(shape(state), [["call", "Searched the web", "completed"]]);
  assert.equal(selectActionCount(state), 1);
});

test("legacy calls match by call id, and only when exactly one candidate could be the one", () => {
  const b = builder(); // legacy: call_id only
  const a = b.start("fetch_webpage", "a.example.com");
  const c = b.start("fetch_webpage", "b.example.com");
  b.finish(c); b.finish(a, { outcome: "failed" });
  assert.deepEqual(selectCalls(feed(b.events)).map((call) => [call.detail, call.outcome, call.ambiguous]), [["a.example.com", "failed", false], ["b.example.com", "completed", false]]);

  // Without call ids two parallel starts cannot be told apart: the finish keeps its own row, flagged, and nothing is guessed.
  const n = builder();
  n.start("fetch_webpage", "a.example.com"); n.start("fetch_webpage", "b.example.com");
  n.add("tool.finished", { tool: "fetch_webpage", status: "ok" });
  for (const event of n.events) delete event.payload.call_id;
  const calls = selectCalls(feed(n.events));
  assert.deepEqual(calls.map((call) => [call.detail ?? null, call.outcome, call.ambiguous]), [["a.example.com", "running", false], ["b.example.com", "running", false], [null, "completed", true]]);
  assert.equal(selectActionCount(feed(n.events)), 3);

  // A finish whose call id matches no start is its own row too (not flagged: there was nothing to confuse it with).
  const m = builder();
  m.start("fetch_webpage", "a.example.com", { callId: "run:1" });
  m.add("tool.finished", { tool: "fetch_webpage", status: "ok", call_id: "run:zzz" });
  assert.deepEqual(selectCalls(feed(m.events)).map((call) => [call.outcome, call.ambiguous]), [["running", false], ["completed", false]]);

  // A single unambiguous candidate settles even without call ids.
  const s = builder();
  s.start("web_search", "q"); s.add("tool.finished", { tool: "web_search", status: "error" });
  for (const event of s.events) delete event.payload.call_id;
  assert.deepEqual(shape(feed(s.events)), [["call", "Web search failed", "failed"]]);
});

test("legacy ids: a call without a persisted id is identified by its start sequence", () => {
  const b = builder();
  b.add("task.created");
  b.call("web_search", "q");
  const state = feed(b.events);
  assert.equal(selectCalls(state)[0].id, "call:2");
  assert.equal(selectCalls(state)[0].toolCallId, null);
  assert.equal(selectCalls(state)[0].detailable, false);
});

test("outcomes: completed, failed, blocked, cancelled and unknown are distinct", () => {
  const b = modern();
  for (const outcome of ["completed", "failed", "blocked", "cancelled", "unknown"]) b.finish(b.start("fetch_webpage", `${outcome}.example.com`), { outcome });
  const calls = selectCalls(feed(b.events));
  assert.deepEqual(calls.map((call) => [call.outcome, call.label, call.tone, call.icon]), [
    ["completed", "Read a web page", "success", "success"],
    ["failed", "Couldn't read a web page", "danger", "failed"],
    ["blocked", "Couldn't read a web page: not allowed", "warning", "blocked"],
    ["cancelled", "Stopped reading a web page", "neutral", "stopped"],
    ["unknown", "Reading a web page may not have finished", "warning", "unknown"],
  ]);
});

test("test calls read their evidence: a tool that returned ok can still be failing tests", () => {
  const b = modern();
  b.finish(b.start("run_tests", "python_pytest"), { exit_code: 1, qualified: true });
  b.finish(b.start("run_tests", "python_pytest"), { exit_code: 0, qualified: true });
  b.finish(b.start("run_tests", "python_pytest"), { exit_code: 0, qualified: false });
  b.finish(b.start("run_tests", "python_pytest"), { timed_out: true });
  b.finish(b.start("run_sandbox_command", "node_build"), { exit_code: 0, qualified: true });
  b.finish(b.start("run_sandbox_command", "node_build"), { exit_code: 2 });
  const calls = selectCalls(feed(b.events));
  assert.deepEqual(calls.map((call) => [call.label, call.tone, call.outcome]), [
    ["Tests failed", "danger", "completed"], ["Tests passed", "success", "completed"], ["Ran tests (result not qualified)", "warning", "completed"],
    ["Tests timed out", "danger", "completed"], ["Build passed", "success", "completed"], ["Build failed", "danger", "completed"],
  ]);
  assert.deepEqual([calls[0].exitCode, calls[0].qualified, calls[3].timedOut], [1, true, true]);
  assert.ok(calls.every((call) => call.detailable && call.presentation === "execution"));
});

test("hidden tools never appear or count; unlisted registered tools render generically and count", () => {
  const b = modern();
  b.call("read_task_input", "goal"); b.call("submit_result"); b.call("unknown");
  b.call("fancy_new_tool"); b.call("web_search", "q");
  const state = feed(b.events);
  assert.deepEqual(shape(state), [["call", "Used fancy new tool", "completed"], ["call", "Searched the web", "completed"]]);
  assert.equal(selectActionCount(state), 2);
  // The compat timeline keeps hiding tools the registry does not list.
  assert.deepEqual(buildActivity(b.events).map((item) => item.label), ["Searched the web"]);
  assert.deepEqual(shape(feed(b.events, { unlistedTools: "hide" })), [["call", "Searched the web", "completed"]]);
});

test("the current call is the newest running one; the others stay running", () => {
  const b = modern();
  const a = b.start("fetch_webpage", "a.example.com");
  const c = b.start("fetch_webpage", "b.example.com");
  let state = feed(b.events);
  assert.equal(selectCurrentCallId(state), `call:${c.tool_call_id}`);
  assert.equal(selectHasRunning(state), true);
  assert.deepEqual(selectCalls(state).map((call) => call.outcome), ["running", "running"]);
  state = add(state, more(b, (next) => next.finish(c)));
  assert.equal(selectCurrentCallId(state), `call:${a.tool_call_id}`);
  assert.equal(selectCall(state, a.tool_call_id).outcome, "running");
});

// Notes and phases ---------------------------------------------------------------------------------------------------

test("note-only activity is activity", () => {
  const b = modern();
  b.note("I looked into it and no tools were needed.", 1); b.note("   ", 2); b.note("Second note", 3);
  const state = feed(b.events);
  assert.equal(selectActionCount(state), 0);
  assert.equal(selectNoteCount(state), 2);
  assert.equal(selectHasActivity(state), true);
  assert.equal(selectCommittedEpoch(state), 3); // even an empty note commits its epoch
  assert.equal(selectHasActivity(createActivityState("task-1")), false);
});

test("phases: the main work has no heading, a change of phase does, stages of one kind share one", () => {
  const b = modern();
  b.phase("builtin/researcher", "s1"); b.call("web_search", "q"); b.phaseEnd("s1");
  b.phase("builtin/verifier", "s2"); b.phaseEnd("s2", "FAILED");
  b.phase("builtin/verifier", "s3"); b.phaseEnd("s3");
  const state = feed(b.events);
  assert.deepEqual(shape(state), [["call", "Searched the web", "completed"], ["phase", "Checking the result"]]);
  assert.equal(selectEntries(state)[1].state, "failed");
  assert.equal(selectEntries(state)[1].id, "phase:s2");
});

test("workspace and repair milestones are lines, not actions", () => {
  const state = feed(codingRun({ style: "modern" }));
  assert.deepEqual(selectEntries(state).filter((entry) => entry.kind === "milestone").map((entry) => entry.label), ["Prepared the workspace", "Fixing failing tests"]);
  assert.equal(selectActionCount(state), 13);
});

// Terminal reconciliation ------------------------------------------------------------------------------------------------

test("when the task is over, calls that never reported back render as unknown, never as running", () => {
  const b = modern();
  b.start("web_search", "q");
  b.finish(b.start("fetch_webpage", "a.example.com"));
  const running = feed(b.events);
  assert.equal(selectHasRunning(running), true);
  assert.deepEqual(shape(running), [["call", "Searching the web", "running"], ["call", "Read a web page", "completed"]]);

  const last = b.add("task.finished", { status: "COMPLETED" });
  const over = add(running, last);
  assert.equal(isSettled(over), true);
  assert.deepEqual(shape(over), [["call", "Searching the web may not have finished", "unknown"], ["call", "Read a web page", "completed"]]);
  assert.equal(selectHasRunning(over), false);
  assert.equal(selectCurrentCallId(over), null);
  assert.deepEqual([selectEntries(over)[0].tone, selectEntries(over)[0].icon], ["warning", "unknown"]);
  assert.equal(selectActionCount(over), 2);
});

test("terminal reconciliation waits until every event up to the end has been read", () => {
  const b = modern();
  const handle = b.start("web_search", "q");
  b.finish(handle);
  const [start, finish] = b.events;
  // The task is known to be over at sequence 2, but only the start has arrived: the finish may still be coming.
  let state = add(createActivityState("task-1"), start);
  state = reduceActivity(state, { type: "terminal", status: "CANCELLED", sequence: 2 });
  assert.equal(isSettled(state), false);
  assert.equal(selectHasRunning(state), true);
  assert.equal(shape(state)[0][2], "running");
  state = add(state, finish);
  assert.equal(isSettled(state), true);
  assert.deepEqual(shape(state), [["call", "Searched the web", "completed"]]); // the real outcome wins: nothing is invented
  // With no known end sequence the status alone settles it.
  const open = add(createActivityState("task-1"), start);
  const closed = reduceActivity(open, { type: "terminal", status: "FAILED" });
  assert.deepEqual(shape(closed), [["call", "Searching the web may not have finished", "unknown"]]);
  assert.equal(reduceActivity(closed, { type: "terminal", status: "FAILED" }), closed);
});

test("unknown is derived for display only: a late finish still settles the real call", () => {
  const b = modern();
  const handle = b.start("web_search", "q");
  const over = reduceActivity(feed(b.events), { type: "terminal", status: "COMPLETED" });
  assert.equal(shape(over)[0][2], "unknown");
  assert.equal(shape(add(over, more(b, (next) => next.finish(handle))))[0][2], "completed");
});

test("running phases are done once the task is over", () => {
  const b = modern();
  b.phase("builtin/verifier", "s1");
  const state = feed(b.events);
  assert.equal(selectEntries(state)[0].state, "running");
  assert.equal(selectEntries(reduceActivity(state, { type: "terminal", status: "COMPLETED" }))[0].state, "done");
});

// Groups -----------------------------------------------------------------------------------------------------------------

test("a run of finished reads folds into a counted group that keeps every call inspectable", () => {
  const b = modern();
  const handles = Array.from({ length: 4 }, () => b.call("read_research_source"));
  b.call("web_search", "q");
  const state = feed(b.events);
  const [group, search] = selectEntries(state);
  assert.equal(group.kind, "group");
  assert.deepEqual([group.label, group.count, group.tool], ["Read 4 sources", 4, "read_research_source"]);
  assert.deepEqual(group.calls.map((call) => call.toolCallId), handles.map((handle) => handle.tool_call_id));
  assert.ok(group.calls.every((call) => call.outcome === "completed" && call.label === "Read a source"));
  assert.equal(search.kind, "call");
  assert.equal(selectActionCount(state), 5);
  assert.deepEqual(selectCalls(state).map((call) => call.tool), ["read_research_source", "read_research_source", "read_research_source", "read_research_source", "web_search"]);
  assert.equal(selectCall(state, handles[2].tool_call_id).label, "Read a source");
});

test("a group grows in place and keeps its identity and key", () => {
  const b = modern();
  b.call("read_research_source"); b.call("read_research_source");
  const two = feed(b.events);
  const groupAt2 = selectEntries(two)[0];
  const three = add(two, more(b, (next) => next.call("read_research_source")));
  const groupAt3 = selectEntries(three)[0];
  assert.deepEqual([groupAt2.count, groupAt3.count], [2, 3]);
  assert.equal(groupAt2.id, groupAt3.id);
  assert.equal(groupAt2.key, groupAt3.key);
  assert.equal(groupAt3.calls[0], groupAt2.calls[0], "the calls already in it are the same objects");
  // The first call keeps its React key when it folds into a group.
  const single = feed(b.events.slice(0, 2));
  assert.equal(selectEntries(single)[0].kind, "call");
  assert.equal(selectEntries(single)[0].key, selectEntries(three)[0].key);
});

test("folding respects order, notes, running calls, details and bad results", () => {
  const b = modern();
  b.call("read_research_source");
  b.note("a note between", 1);
  b.call("read_research_source");
  const a = b.start("read_research_source");           // running: does not fold
  b.call("read_research_source", "has-detail");         // own detail: does not fold
  b.finish(b.start("get_test_results"), { exit_code: 1 }); // a failing result stays on its own line
  b.finish(b.start("get_test_results"), { exit_code: 0, qualified: true });
  b.finish(b.start("get_test_results"), { exit_code: 0, qualified: true });
  let state = feed(b.events);
  assert.deepEqual(shape(state), [
    ["call", "Read a source", "completed"], ["note", "a note between"], ["call", "Read a source", "completed"],
    ["call", "Reading a source", "running"], ["call", "Read a source", "completed"],
    ["call", "Checked test results: failed", "completed"], ["group", "Checked test results 2 times", 2],
  ]);
  // The running call finishes next to a finished one and folds with it; the call with its own detail keeps the run apart.
  state = add(state, more(b, (next) => next.finish(a)));
  assert.deepEqual(shape(state), [
    ["call", "Read a source", "completed"], ["note", "a note between"], ["group", "Read 2 sources", 2],
    ["call", "Read a source", "completed"], ["call", "Checked test results: failed", "completed"], ["group", "Checked test results 2 times", 2],
  ]);
  assert.equal(selectEntries(state)[3].detail, "has-detail");
});

test("parallel calls fold into one group when the last of them finishes", () => {
  const b = modern();
  const a = b.start("read_research_source");
  const c = b.start("read_research_source");
  const d = b.start("read_research_source");
  b.finish(d); b.finish(a);
  let state = feed(b.events);
  assert.deepEqual(shape(state), [["call", "Read a source", "completed"], ["call", "Reading a source", "running"], ["call", "Read a source", "completed"]]);
  state = add(state, more(b, (next) => next.finish(c)));
  assert.deepEqual(shape(state), [["group", "Read 3 sources", 3]]);
  assert.deepEqual(selectEntries(state)[0].calls.map((call) => call.id), [a, c, d].map((handle) => `call:${handle.tool_call_id}`));
});

test("the compat timeline folds exactly as the old builder did", () => {
  const items = buildActivity(researchRun());
  assert.deepEqual(items.filter((item) => item.count).map((item) => [item.label, item.count]), [["Read 4 sources", 4], ["Read 2 sources", 2]]);
});

// Counts -----------------------------------------------------------------------------------------------------------------

for (const total of [40, 41, 500, 2000]) {
  test(`exact action counts beyond the old 40-step cap: ${total} calls`, () => {
    const b = modern();
    const tools = ["web_search", "fetch_webpage", "read_repo_file", "search_repo"];
    for (let i = 0; i < total; i++) b.call(tools[i % tools.length], `target-${i}`);
    const state = feed(b.events);
    assert.equal(selectActionCount(state), total);
    assert.equal(selectCalls(state).length, total);
    assert.equal(selectEntries(state).length, total);
  });
}

test("exact counts when most calls fold into groups", () => {
  const b = modern();
  for (let i = 0; i < 700; i++) b.call("read_research_source");
  b.call("web_search", "q");
  const state = feed(b.events);
  assert.equal(selectActionCount(state), 701);
  assert.equal(selectEntries(state).length, 2);
  assert.equal(selectEntries(state)[0].count, 700);
  assert.equal(selectCalls(state).length, 701);
});

test("the count equals the number of actions the presentation rules show, hidden tools excluded", () => {
  const events = codingRun({ style: "modern" });
  const state = feed(events);
  const extra = modern({ start: events.length });
  extra.call("read_task_input", "goal"); extra.call("read_task_input", "history");
  const after = add(state, extra.events);
  assert.equal(selectActionCount(after), selectActionCount(state));
  assert.equal(selectActionCount(after), selectCalls(after).length);
});

// Purity, identity, scale -----------------------------------------------------------------------------------------

test("the reducer never mutates its input state", () => {
  const events = researchRun({ style: "modern" });
  let state = deepFreeze(feed(events.slice(0, 12)));
  state = deepFreeze(add(state, events.slice(12, 20)));
  state = deepFreeze(add(state, events.slice(0, 4)));            // already seen
  state = deepFreeze(add(state, events.slice(20)));
  state = deepFreeze(reduceActivity(state, { type: "terminal", status: "COMPLETED", sequence: events.length }));
  assert.equal(selectContiguous(state), events.length);
  // And a replay out of order after everything is frozen.
  const gap = deepFreeze(add(createActivityState("task-1"), events.slice(10)));
  deepFreeze(add(gap, events.slice(0, 10)));
});

test("entries a new event does not touch keep their object identity", () => {
  const b = modern();
  b.note("n", 1); b.call("web_search", "q"); b.call("fetch_webpage", "a.example.com");
  const before = feed(b.events);
  const after = add(before, more(b, (next) => next.call("read_repo_file", "src/a.py")));
  const old = selectEntries(before);
  selectEntries(after).slice(0, old.length).forEach((entry, index) => assert.equal(entry, old[index], `entry ${index}`));
  assert.equal(selectEntries(after).length, old.length + 1);
});

test("identity also survives an out-of-order replay", () => {
  const events = codingRun({ style: "modern" });
  const withoutMiddle = events.filter((event) => event.sequence !== 10);
  const before = feed(withoutMiddle);
  const after = add(before, events.filter((event) => event.sequence === 10));
  const old = new Map(selectEntries(before).map((entry) => [entry.id, entry]));
  let reused = 0;
  for (const entry of selectEntries(after)) if (old.get(entry.id) === entry) reused++;
  assert.ok(reused >= selectEntries(before).length - 3, `only ${reused} of ${selectEntries(before).length} entries were reused`);
  assert.deepEqual(shape(after), shape(feed(events)));
});

test("settled views are stable too: reconciling does not remake entries, and the unknown form of a call is cached", () => {
  const b = modern();
  b.call("web_search", "q"); b.start("fetch_webpage", "a.example.com");
  const open = feed(b.events);
  const over = reduceActivity(open, { type: "terminal", status: "FAILED" });
  assert.equal(selectEntries(over)[0], selectEntries(open)[0], "the settled call is untouched");
  assert.equal(selectEntries(over)[1].outcome, "unknown");
  const later = add(over, more(b, (next) => next.note("after the fact", 9)));
  assert.equal(selectEntries(later)[1], selectEntries(over)[1], "the same unknown object, not a new one per render");
  const again = reduceActivity(over, { type: "terminal", status: "FAILED" });
  assert.equal(selectEntries(again), selectEntries(over));
});

test("performance: 5,000 events reduce well under a second, and one more event does not rebuild the rest", () => {
  const b = modern();
  const tools = ["web_search", "fetch_webpage", "read_repo_file", "search_repo", "list_repo_files"];
  for (let i = 0; i < 2200; i++) { b.call(tools[i % tools.length], `t-${i}`); if (i % 3 === 0) b.note(`note ${i}`, i + 1); if (i % 7 === 0) b.bookkeeping(); }
  assert.ok(b.events.length > 5000, `fixture is ${b.events.length} events`);
  let started = performance.now();
  const state = feed(b.events);
  const batch = performance.now() - started;
  assert.ok(batch < 1000, `one batch of ${b.events.length} events took ${batch.toFixed(0)}ms`);
  assert.equal(selectActionCount(state), 2200);

  started = performance.now();
  let streamed = createActivityState("task-1");
  for (const event of b.events) streamed = add(streamed, event);
  const oneByOne = performance.now() - started;
  assert.ok(oneByOne < 1500, `${b.events.length} single-event reductions took ${oneByOne.toFixed(0)}ms`);
  assert.deepEqual(shape(streamed), shape(state));

  const tail = more(b, (next) => next.call("web_search", "one more"));
  started = performance.now();
  const after = add(state, tail);
  const append = performance.now() - started;
  assert.ok(append < 50, `appending one call to ${b.events.length} events took ${append.toFixed(1)}ms`);
  const old = selectEntries(state);
  assert.ok(selectEntries(after).slice(0, old.length).every((entry, index) => entry === old[index]), "untouched entries must be the same objects");
});

test("the event set the reducer follows is the one the components always used, plus the approval events behind the Approve / Decline card", () => {
  assert.deepEqual([...activityEventTypes].sort(), ["approval.decided", "approval.required", "assistant.message", "sandbox.workspace.created", "subagent.finished", "subagent.started", "task.repair.started", "tool.finished", "tool.started"]);
});

test("approval events add no row of their own", () => {
  const events = [
    { task_id: "t", sequence: 1, event_type: "approval.required", payload: { approval_id: "a", tool: "bash", detail: "ls", state: "PENDING" }, created_at: "" },
    { task_id: "t", sequence: 2, event_type: "approval.decided", payload: { approval_id: "a", tool: "bash", state: "APPROVED" }, created_at: "" },
  ];
  const state = reduceActivity(createActivityState("t"), { type: "events", events });
  assert.equal(selectEntries(state).length, 0);
  assert.equal(selectActionCount(state), 0);
});
