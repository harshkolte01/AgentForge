// While a run is going, finished steps before the newest few fold into one "N earlier steps" line.
import assert from "node:assert/strict";
import { test } from "node:test";
import { MIN_EARLIER, VISIBLE_STEPS, chunkRows, collapseRows, sectionsOf } from "../features/sessions/timeline-model.ts";
import { summarizeTools } from "../lib/activity/presentation.ts";
import { createActivityState, reduceActivity, selectEntries } from "../lib/activity/reducer.ts";

let clock = 0;
const ev = (event_type, payload) => ({ task_id: "task-1", sequence: ++clock, event_type, payload, created_at: new Date(Date.UTC(2026, 9, 12, 0, 0, clock)).toISOString() });
const start = (tool, id, extra = {}) => ev("tool.started", { tool, tool_call_id: id, agent_run_id: "parent", ...extra });
const finish = (tool, id, outcome = "completed", extra = {}) => ev("tool.finished", { tool, tool_call_id: id, agent_run_id: "parent", outcome, ...extra });
/** A settled call that has a detail, so it stays a row of its own instead of folding into a counted group. */
const done = (tool, n) => [start(tool, `${tool}${n}`, { detail: `file${n}.ts` }), finish(tool, `${tool}${n}`)];
const chunksOf = (events) => chunkRows(selectEntries(reduceActivity(createActivityState("task-1"), { type: "events", events })));
const reads = (count, from = 0) => Array.from({ length: count }, (_, i) => done("read", from + i)).flat();
const kinds = (slots) => slots.map((slot) => slot.kind === "earlier" ? `earlier(${slot.count})` : slot.chunk.kind === "subagents" ? "team" : slot.chunk.entry.outcome);

test("many finished steps fold into one line and the newest four stay", () => {
  clock = 0;
  const chunks = chunksOf(reads(20));
  assert.equal(chunks.length, 20);
  const slots = collapseRows(chunks);
  assert.deepEqual(kinds(slots), ["earlier(16)", "completed", "completed", "completed", "completed"]);
  assert.deepEqual(slots.slice(1).map((slot) => slot.chunk.key), chunks.slice(-VISIBLE_STEPS).map((chunk) => chunk.key));
  assert.equal(slots[0].summary, "Read 16 files");
  assert.equal(slots[0].chunks.length, 16);
});

test("a short list is left alone: a line for one or two steps would hide more than it saves", () => {
  clock = 0;
  for (const count of [0, 1, VISIBLE_STEPS, VISIBLE_STEPS + MIN_EARLIER - 1]) {
    const slots = collapseRows(chunksOf(reads(count)));
    assert.equal(slots.length, count, `${count} steps`);
    assert.ok(slots.every((slot) => slot.kind === "chunk"));
    clock = 0;
  }
  assert.deepEqual(kinds(collapseRows(chunksOf(reads(VISIBLE_STEPS + MIN_EARLIER)))), [`earlier(${MIN_EARLIER})`, "completed", "completed", "completed", "completed"]);
});

test("the line keeps its key and stays one line as steps join it, so a person's choice to open it is kept", () => {
  clock = 0;
  const events = reads(10);
  const before = collapseRows(chunksOf(events));
  const after = collapseRows(chunksOf([...events, ...reads(3, 10)]));
  assert.equal(before[0].key, after[0].key);
  assert.equal(after[0].key, `earlier:${before[0].chunks[0].key}`);
  assert.equal(after[0].count, before[0].count + 3);
  assert.equal(after.length, before.length);
});

test("a step that is running, failed, refused or unfinished is never folded away, and splits the stretch", () => {
  clock = 0;
  const events = [...reads(5), start("bash", "slow", { detail: "npm test" }), ...reads(6, 5), ...[
    start("grep", "bad", { detail: "x" }), finish("grep", "bad", "failed")], ...reads(6, 11), ...reads(4, 20)];
  const slots = collapseRows(chunksOf(events));
  assert.deepEqual(kinds(slots), ["earlier(5)", "running", "earlier(6)", "failed", "earlier(6)", "completed", "completed", "completed", "completed"]);
  for (const slot of slots) if (slot.kind === "earlier") assert.ok(slot.chunks.every((chunk) => chunk.entry.outcome === "completed"));
});

test("a command that finished but failed (a nonzero exit) stays on screen", () => {
  clock = 0;
  const events = [...reads(4), start("bash", "t", { detail: "npm test" }), finish("bash", "t", "completed", { exit_code: 1 }), ...reads(6, 4)];
  const slots = collapseRows(chunksOf(events));
  const failed = slots.find((slot) => slot.kind === "chunk" && slot.chunk.entry.tone === "danger");
  assert.ok(failed, "the failed command is a row of its own");
});

test("a team of subagents is never folded away", () => {
  clock = 0;
  const events = [start("delegate_task", "d1", { child_run_id: "c1", detail: "Auth" }), finish("delegate_task", "d1"), ...reads(12)];
  const slots = collapseRows(chunksOf(events));
  assert.deepEqual(kinds(slots).slice(0, 2), ["team", "earlier(8)"]);
});

test("settled groups fold with the calls inside them counted", () => {
  clock = 0;
  const plain = (n) => [start("read", `g${n}`), finish("read", `g${n}`)]; // no detail: consecutive ones fold into "Read N files"
  const chunks = chunksOf([...plain(1), ...plain(2), ...plain(3), ...done("grep", 1), ...done("grep", 2), ...done("grep", 3), ...reads(5, 10)]);
  const slots = collapseRows(chunks);
  assert.equal(slots[0].kind, "earlier");
  assert.equal(slots[0].count, 3 + 3 + 5 - 4);
});

test("the summary names what the steps did, most frequent first, and counts the rest", () => {
  assert.equal(summarizeTools(["read", "read", "grep", "read"]), "Read 3 files · Ran 1 search");
  assert.equal(summarizeTools(["bash"]), "Ran 1 command");
  assert.equal(summarizeTools(["read", "read", "grep", "grep", "bash", "edit", "web_search"]), "Read 2 files · Ran 2 searches · Ran 1 command · 2 more");
  assert.equal(summarizeTools(["use_skill", "some_plugin__thing"]), "2 other steps");
  assert.equal(summarizeTools(["read", "use_skill"]), "Read 1 file · 1 more");
  assert.equal(summarizeTools([]), "");
});

test("messages are never folded or moved: only the tools between two messages fold, each burst on its own", () => {
  clock = 0;
  const note = (text) => ev("assistant.message", { text });
  const entries = selectEntries(reduceActivity(createActivityState("task-1"), { type: "events", events: [
    ...done("read", 1), note("one"), ...done("grep", 2), note("two"), ...reads(12, 10), note("three"), ...reads(2, 30), note("four") ] }));
  const shape = sectionsOf(entries).map((section) => {
    if (section.kind === "note") return `note:${section.entry.text}`;
    return collapseRows(chunkRows(section.entries)).map((slot) => slot.kind === "earlier" ? `earlier(${slot.count})` : "step").join(",");
  });
  // A single tool, a single tool, a burst of twelve, two tools: only the burst folds, and the four messages stay in order.
  assert.deepEqual(shape, ["step", "note:one", "step", "note:two", "earlier(8),step,step,step,step", "note:three", "step,step", "note:four"]);
});
