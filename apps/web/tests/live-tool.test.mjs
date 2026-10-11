import assert from "node:assert/strict";
import { test } from "node:test";
import { applySnapshot, createLiveState, emptyLive, isToolKind, liveHandoff, liveToolOf, parseLiveDelta, reduceLive, visibleLive } from "../lib/activity/live-text.ts";
import { createActivityState, reduceActivity, selectEntries } from "../lib/activity/reducer.ts";
import { describeDraft, toolPresentation } from "../lib/activity/presentation.ts";
import { heldAsLive, holdLive, placeLiveTool } from "../features/sessions/timeline-model.ts";
import { builder } from "./fixtures/streams.mjs";
import { fakeServer, makeSync } from "./helpers/sync-harness.mjs";

const CALL = "run-1:tool:call_abc";
const written = (extra) => ({ task_id: "task-1", epoch: 2, revision: 1, kind: "tool", offset: 0, text: "", reset: true, tool: "write", call_id: CALL, detail: "src/a.py", ...extra });
const printing = (extra) => written({ kind: "output", tool: "bash", detail: null, ...extra });
const apply = (state, ...frames) => frames.reduce((current, next) => reduceLive(current, next).state, state);
const entriesOf = (events) => selectEntries(reduceActivity(createActivityState("task-1"), { type: "events", events }));

// The wire -----------------------------------------------------------------------------------------------------------------

test("a tool frame is parsed with the call it belongs to", () => {
  const wire = { task_id: "t", epoch: 2, revision: 1, kind: "tool", offset: 0, text: "print(1)", reset: true, tool: "write", call_id: CALL, detail: "src/a.py" };
  assert.deepEqual(parseLiveDelta(wire), wire);
  assert.deepEqual(parseLiveDelta({ ...wire, kind: "output", tool: "bash", detail: undefined }), { ...wire, kind: "output", tool: "bash", detail: null });
});

test("a tool frame that does not say which call it is, is not shown", () => {
  for (const kind of ["tool", "output"]) {
    assert.equal(parseLiveDelta({ task_id: "t", epoch: 1, offset: 0, text: "x", kind }), null);
    assert.equal(parseLiveDelta({ task_id: "t", epoch: 1, offset: 0, text: "x", kind, tool: "bash" }), null);
    assert.equal(parseLiveDelta({ task_id: "t", epoch: 1, offset: 0, text: "x", kind, call_id: CALL }), null);
  }
});

test("prose frames are exactly what they were: no tool keys, whatever an older or newer server adds", () => {
  const frame = parseLiveDelta({ task_id: "t", epoch: 1, revision: 0, kind: "answer", offset: 0, text: "Hi", reset: true, tool: "bash", call_id: CALL });
  assert.deepEqual(frame, { task_id: "t", epoch: 1, revision: 0, kind: "answer", offset: 0, text: "Hi", reset: true });
});

// The reducer --------------------------------------------------------------------------------------------------------------

test("a call being written keeps its identity as its text grows", () => {
  let state = reduceLive(createLiveState(), written({ text: "print(1)\n" })).state;
  assert.deepEqual(state.tool, { name: "write", callId: CALL, detail: "src/a.py" });
  state = reduceLive(state, { ...written({ offset: 9, text: "print(2)\n", reset: false }), tool: undefined, call_id: undefined }).state; // appends may omit it
  assert.deepEqual([state.text, state.kind, state.tool?.callId], ["print(1)\nprint(2)\n", "tool", CALL]);
  assert.deepEqual(liveToolOf(state), { name: "write", callId: CALL, detail: "src/a.py", phase: "tool", text: "print(1)\nprint(2)\n" });
});

test("the call running replaces the call written: a newer version with its own identity", () => {
  const before = apply(createLiveState(), written({ text: "ls -la" }));
  const after = reduceLive(before, printing({ epoch: 3, revision: 2, text: "" }));
  assert.equal(after.status, "reset");
  assert.deepEqual([after.state.kind, after.state.tool], ["output", { name: "bash", callId: CALL, detail: null }]);
  assert.equal(liveToolOf(apply(after.state, printing({ epoch: 3, revision: 2, offset: 0, text: "total 8\n", reset: false }))).text, "total 8\n");
});

test("a clear removes the call: an empty reset of a prose kind carries none", () => {
  const state = apply(createLiveState(), printing({ text: "output" }));
  const cleared = reduceLive(state, { task_id: "task-1", epoch: 2, revision: 2, kind: "progress", offset: 0, text: "", reset: true });
  assert.deepEqual([cleared.state.kind, cleared.state.tool, liveToolOf(cleared.state)], ["progress", undefined, null]);
});

test("output that moves as a window (a replacement each time) is simply the newest text", () => {
  let state = apply(createLiveState(), printing({ text: "line 1\nline 2\n" }));
  state = apply(state, printing({ revision: 2, text: "…\nline 2\nline 3\n" }));
  assert.equal(liveToolOf(state).text, "…\nline 2\nline 3\n");
});

test("a snapshot restores the call too, and a newer prose row clears it", () => {
  const snapshot = { task_id: "task-1", epoch: 2, revision: 1, kind: "output", text: "so far", tool: "bash", call_id: CALL, detail: null, event_sequence: 5, terminal: false };
  const state = applySnapshot(emptyLive, snapshot);
  assert.deepEqual(liveToolOf(state), { name: "bash", callId: CALL, detail: null, phase: "output", text: "so far" });
  const prose = applySnapshot(state, { ...snapshot, revision: 2, kind: "progress", text: "", tool: null, call_id: null });
  assert.equal(liveToolOf(prose), null);
  // Older servers send no tool fields at all: such a row is not a call.
  assert.equal(liveToolOf(applySnapshot(emptyLive, { task_id: "task-1", epoch: 1, revision: 0, kind: "tool", text: "x", event_sequence: 0, terminal: false })), null);
});

test("a call's text is never prose: it is not handed off to a note, held, or shown as live words", () => {
  const state = apply(createLiveState(), written({ text: "ls -la" }));
  assert.equal(isToolKind(state.kind), true);
  assert.deepEqual(liveHandoff(state, 0, null), { text: "", handedOffTo: null });
  assert.equal(visibleLive(state, 0, null), "");
  assert.equal(holdLive(null, state, false), null);
  const held = holdLive(null, { epoch: 1, kind: "answer", text: "kept" }, false);
  assert.equal(holdLive(held, state, true), held, "after the run, a call frame leaves what was held alone");
  assert.equal(holdLive(held, state, false), null, "during the run it is not words to keep");
  assert.deepEqual(heldAsLive(held), { epoch: 1, revision: 0, kind: "answer", text: "kept", length: 4 });
});

test("the recovery controller carries a call from the stream and clears it", async () => {
  const server = fakeServer();
  const { controller, clock, streams, snap } = makeSync({ server });
  controller.start();
  await clock.advance(0);
  streams.open();
  streams.delta(written({ text: "echo hi" }));
  assert.deepEqual(liveToolOf(snap().live), { name: "write", callId: CALL, detail: "src/a.py", phase: "tool", text: "echo hi" });
  streams.delta(written({ offset: 7, text: " there", reset: false }));
  assert.equal(liveToolOf(snap().live).text, "echo hi there");
  streams.delta({ task_id: "task-1", epoch: 2, revision: 2, kind: "progress", offset: 0, text: "", reset: true });
  assert.equal(liveToolOf(snap().live), null);
  controller.stop();
});

// Where it shows -----------------------------------------------------------------------------------------------------------

const view = (phase, extra) => ({ name: phase === "tool" ? "write" : "bash", callId: CALL, detail: null, phase, text: "text", ...extra });

test("nothing live shows nothing", () => {
  assert.equal(placeLiveTool([], null, false), null);
});

test("a call being written gets a row of its own until its real row exists", () => {
  const draft = view("tool");
  assert.deepEqual(placeLiveTool([], draft, false), { kind: "draft", call: draft });
  const b = builder({ style: "modern" });
  b.start("write", "src/a.py", { callId: CALL });
  assert.equal(placeLiveTool(entriesOf(b.events), draft, false), null, "the real row has taken over: never both");
});

test("a call waiting for the person's approval is shown by its card, not a second time", () => {
  assert.equal(placeLiveTool([], view("tool"), true), null);
});

test("a running command's output shows under its own row, only while it is running", () => {
  const b = builder({ style: "modern" });
  const handle = b.start("bash", "npm test", { callId: CALL });
  const running = entriesOf(b.events);
  const output = view("output", { text: "ok 1\nok 2\n" });
  assert.deepEqual(placeLiveTool(running, output, false), { kind: "output", entryId: running[0].id, text: "ok 1\nok 2\n" });
  assert.equal(placeLiveTool(running, view("output", { text: "" }), false), null, "nothing printed yet");
  assert.equal(placeLiveTool(running, view("output", { callId: "someone-else" }), false), null, "another call's row is not this one");
  assert.equal(placeLiveTool([], output, false), null, "its row has not arrived yet");
  b.finish(handle);
  assert.equal(placeLiveTool(entriesOf(b.events), output, false), null, "a settled row has its own saved details");
});

// The words ----------------------------------------------------------------------------------------------------------------

test("a call being written never says it is running", () => {
  assert.deepEqual(describeDraft("bash", null), { label: "Writing a command" });
  assert.deepEqual(describeDraft("write", "src/a.py"), { label: "Writing a file", detail: "src/a.py" });
  assert.deepEqual(describeDraft("edit", "src/a.py"), { label: "Editing a file", detail: "src/a.py" });
  assert.deepEqual(describeDraft("delegate_task", "Explore repository data"), { label: "Briefing a subagent", detail: "Explore repository data" });
  assert.deepEqual(describeDraft("create_pull_request", "Fix it"), { label: "Writing a pull request", detail: "Fix it" });
  assert.deepEqual(describeDraft("create_task_artifact", "Report"), { label: "Writing a document", detail: "Report" });
  assert.equal(toolPresentation("bash"), "execution");
});

test("a tool the registry does not list still shows, with a generic wording, and internal tools never do", () => {
  assert.deepEqual(describeDraft("some_new_tool", null), { label: "Using some new tool" });
  assert.equal(describeDraft("read_task_input", null), null);
});
