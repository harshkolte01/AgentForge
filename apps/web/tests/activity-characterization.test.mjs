// Characterization of the timeline builder (`lib/activity.ts`) on realistic event streams, captured BEFORE the reducer
// refactor. They pin labels, folding, ids and running/done/failed states of the legacy wire format, which the compat
// layer must keep producing. Where the new model deliberately differs, the test says so.
import assert from "node:assert/strict";
import { test } from "node:test";
import { activityEventTypes, buildActivity as build, committedEpoch, hasRunningStep, stepCount } from "../lib/activity.ts";
import { cancellationRun, codingRun, failureRun, legacyNoCallIds, researchRun } from "./fixtures/streams.mjs";

// Components hand buildActivity only the activity events (`activityEventTypes`), never task.finished; do the same here.
// (Given task.finished, the reducer behind it now also settles unfinished calls as unknown: see activity-reducer.test.mjs.)
const buildActivity = (events) => build(events.filter((event) => activityEventTypes.has(event.event_type)));
const rows = (items) => items.map((item) => [item.kind ?? "step", item.label || item.text, item.detail ?? null, item.state, item.count ?? null]);

test("research run: notes, parallel same-tool calls settle by call id, reads fold, the main stage has no heading", () => {
  const items = buildActivity(researchRun());
  assert.deepEqual(rows(items), [
    ["note", "I'll start by searching for the announcement.", null, "done", null],
    ["step", "Searched the web", "“agent harness benchmarks”", "done", null],
    ["note", "Now I'll open the strongest results side by side.", null, "done", null],
    ["step", "Couldn't read a web page", "a.example.com/post", "failed", null],
    ["step", "Read a web page", "b.example.com", "done", null],
    ["step", "Read a web page", "c.example.com/news", "done", null],
    ["step", "Read 4 sources", null, "done", 4],
    ["phase", "Checking the result", null, "done", null],
    ["step", "Read 2 sources", null, "done", 2],
  ]);
  assert.equal(stepCount(items), 10); // 1 search + 3 fetches + 4 + 2 reads; the input read is internal and never counted
  assert.equal(hasRunningStep(items), false);
  assert.equal(committedEpoch(researchRun()), 2);
});

test("research run: ids are the sequence of the event that created the row", () => {
  const ids = buildActivity(researchRun()).map((item) => item.id);
  assert.deepEqual(ids, ["note:5", "tool:6", "note:10", "tool:11", "tool:12", "tool:13", "tool:17", "agent:s2", "tool:27"]);
});

test("coding run: tests that ran are 'Ran tests', a repair is a milestone, repeated results fold", () => {
  const items = buildActivity(codingRun());
  assert.deepEqual(rows(items), [
    ["step", "Prepared the workspace", null, "done", null],
    ["note", "Let me look at the repository layout first.", null, "done", null],
    ["step", "Browsed the repository", "src", "done", null],
    ["step", "Searched the repository", "parse_args", "done", null],
    ["step", "Read a file", "src/cli.py", "done", null],
    ["note", "I'll change the parser and run the tests.", null, "done", null],
    ["step", "Read code", "src/cli.py", "done", null],
    ["step", "Edited code", "src/cli.py", "done", null],
    ["step", "Wrote code", "tests/test_cli.py", "done", null],
    ["step", "Ran tests", "python_pytest", "done", null],
    ["step", "Fixing failing tests", null, "done", null],
    ["step", "Edited code", "src/cli.py", "done", null],
    ["step", "Ran tests", "python_pytest", "done", null],
    ["step", "Reviewed changes", null, "done", null],
    ["step", "Ran a command", "node_build", "done", null],
    ["step", "Checked test results 2 times", null, "done", 2],
  ]);
  assert.equal(stepCount(items), 13); // milestones ("Prepared the workspace", "Fixing failing tests") are not actions
});

test("failure run: failed calls keep their tool's failed wording and a failed phase is marked failed", () => {
  assert.deepEqual(rows(buildActivity(failureRun())), [
    ["step", "Web search failed", "“latest release”", "failed", null],
    ["step", "Couldn't read a web page", "example.com/x", "failed", null],
    ["phase", "Checking the result", null, "failed", null],
  ]);
});

test("cancellation run: a stopped call is a failure; without a terminal event an unfinished call stays running", () => {
  // The new model reconciles unfinished calls once the task is terminal (they become 'unknown'); buildActivity is only
  // ever given activity events (never task.finished), so the compat layer keeps this behaviour.
  const items = buildActivity(cancellationRun());
  assert.deepEqual(rows(items), [
    ["note", "Searching now.", null, "done", null],
    ["step", "Web search failed", "“q”", "failed", null],
    ["step", "Reading a web page", "slow.example.com", "running", null],
  ]);
  assert.equal(hasRunningStep(items), true);
});

test("legacy events without call ids: sequential start/finish pairs match, an unmatched finish becomes its own row", () => {
  assert.deepEqual(rows(buildActivity(legacyNoCallIds())), [
    ["step", "Searched the web", "“q1”", "done", null],
    ["step", "Read a web page", "a.example.com", "done", null],
    ["step", "Reading a web page", "b.example.com", "running", null],
    ["step", "Couldn't read a file", "src/a.py", "failed", null],
    ["step", "Reviewed changes", null, "done", null],
  ]);
});

test("events outside the activity set are ignored, whatever their position", () => {
  const events = researchRun();
  assert.equal(events.length, 32);
  assert.deepEqual(buildActivity(events), buildActivity(events.filter((event) => event.event_type !== "task.created" && event.event_type !== "model.started")));
});
