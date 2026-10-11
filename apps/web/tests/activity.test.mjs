import assert from "node:assert/strict";
import { test } from "node:test";
import { buildActivity, committedEpoch, hasRunningStep, stepCount } from "../lib/activity.ts";

let sequence = 0;
const event = (event_type, payload = {}) => ({ task_id: "t", sequence: ++sequence, event_type, payload, created_at: "2026-01-01T00:00:00Z" });
const started = (tool, detail, call_id) => event("tool.started", { tool, ...(detail ? { detail } : {}), ...(call_id ? { call_id } : {}) });
const finished = (tool, status = "ok", call_id) => event("tool.finished", { tool, status, ...(call_id ? { call_id } : {}) });
const note = (text, epoch) => event("assistant.message", { text, ...(epoch ? { epoch } : {}) });

test("notes and steps keep the order they happened in", () => {
  const items = buildActivity([
    note("I'll search for the announcement.", 1),
    started("web_search", "latest model"), finished("web_search"),
    note("Now I'll open the top result.", 2),
    started("fetch_webpage", "www.anthropic.com/news"), finished("fetch_webpage"),
  ]);
  assert.deepEqual(items.map((item) => [item.kind ?? "step", item.text ?? item.label]), [
    ["note", "I'll search for the announcement."],
    ["step", "Searched the web"],
    ["note", "Now I'll open the top result."],
    ["step", "Read a web page"],
  ]);
  assert.equal(items[1].detail, "“latest model”");
  assert.equal(items[3].detail, "www.anthropic.com/news");
});

test("a step appears as running the moment it starts and settles when it finishes", () => {
  assert.equal(buildActivity([started("web_search", "q")])[0].state, "running");
  assert.equal(buildActivity([started("web_search", "q"), finished("web_search")])[0].state, "done");
  assert.equal(buildActivity([started("web_search", "q"), finished("web_search", "error")])[0].state, "failed");
});

test("internal reads of the request never reach the timeline", () => {
  const items = buildActivity([started("read_task_input", "goal"), finished("read_task_input"), started("read_task_input", "history"), finished("read_task_input")]);
  assert.deepEqual(items, []);
});

test("parallel calls of one tool settle by their own call id", () => {
  const items = buildActivity([
    started("fetch_webpage", "a.example.com", "run:tool:1"), started("fetch_webpage", "b.example.com", "run:tool:2"),
    finished("fetch_webpage", "ok", "run:tool:2"), finished("fetch_webpage", "error", "run:tool:1"),
  ]);
  assert.deepEqual(items.map((item) => [item.detail, item.state]), [["a.example.com", "failed"], ["b.example.com", "done"]]);
});

// Updated for the reducer: a legacy finish used to settle "the latest running step of its tool", which is a guess when
// several run side by side. It now settles a step only when exactly one could be the one, and otherwise keeps its own row.
test("without call ids a finish settles a running step only when exactly one could be it", () => {
  assert.deepEqual(buildActivity([started("fetch_webpage", "a.example.com"), finished("fetch_webpage")]).map((item) => item.state), ["done"]);
  const items = buildActivity([started("fetch_webpage", "a.example.com"), started("fetch_webpage", "b.example.com"), finished("fetch_webpage")]);
  assert.deepEqual(items.map((item) => [item.detail ?? null, item.state]), [["a.example.com", "running"], ["b.example.com", "running"], [null, "done"]]);
});

test("a run of finished reads that say nothing more folds into one counted line", () => {
  const reads = Array.from({ length: 4 }, () => [started("read_research_source"), finished("read_research_source")]).flat();
  const items = buildActivity([...reads, started("web_search", "q"), finished("web_search")]);
  assert.deepEqual(items.map((item) => item.label), ["Read 4 sources", "Searched the web"]);
  assert.equal(stepCount(items), 5);
});

test("steps with their own detail, and running steps, are never folded", () => {
  const items = buildActivity([started("fetch_webpage", "a.example.com"), finished("fetch_webpage"), started("fetch_webpage", "b.example.com"), finished("fetch_webpage"),
    started("read_research_source"), started("read_research_source")]);
  assert.equal(items.length, 4);
});

test("a note between reads keeps them apart", () => {
  const items = buildActivity([started("read_research_source"), finished("read_research_source"), note("Checking another."), started("read_research_source"), finished("read_research_source")]);
  assert.deepEqual(items.map((item) => item.kind ?? item.label), ["Read a source", "note", "Read a source"]);
});

test("notes are not actions", () => {
  assert.equal(stepCount(buildActivity([note("Just thinking out loud."), note("Again.")])), 0);
});

test("the main work needs no heading, so nothing stays 'running' at the top for the whole stage", () => {
  const items = buildActivity([event("subagent.started", { type: "builtin/researcher", spawn_id: "s1" }), started("web_search", "q"), finished("web_search"), event("subagent.finished", { spawn_id: "s1", status: "COMPLETED" })]);
  assert.deepEqual(items.map((item) => item.label), ["Searched the web"]);
});

test("a change of phase is a heading, never a running step", () => {
  const items = buildActivity([
    event("subagent.started", { type: "builtin/researcher", spawn_id: "s1" }), started("web_search", "q"), finished("web_search"), event("subagent.finished", { spawn_id: "s1", status: "COMPLETED" }),
    event("subagent.started", { type: "builtin/verifier", spawn_id: "s2" }), started("read_research_source"), finished("read_research_source"),
  ]);
  assert.deepEqual(items.map((item) => [item.kind ?? "step", item.label]), [["step", "Searched the web"], ["phase", "Checking the result"], ["step", "Read a source"]]);
  assert.equal(hasRunningStep(items), false);
  assert.equal(stepCount(items), 2);
});

test("the stages of one research share one heading", () => {
  const stage = (id) => [event("subagent.started", { type: "builtin/verifier", spawn_id: id }), event("subagent.finished", { spawn_id: id, status: "COMPLETED" })];
  assert.equal(buildActivity([...stage("a"), ...stage("b")]).filter((item) => item.kind === "phase").length, 1);
});

test("only a running step counts as something happening now", () => {
  assert.equal(hasRunningStep(buildActivity([started("web_search", "q")])), true);
  assert.equal(hasRunningStep(buildActivity([note("I'll look."), event("subagent.started", { type: "builtin/verifier", spawn_id: "s" })])), false);
});

test("the newest saved epoch is the one live text must not repeat", () => {
  assert.equal(committedEpoch([note("a", 2), note("b", 5), note("c")]), 5);
  assert.equal(committedEpoch([started("web_search")]), 0);
});

test("empty notes and unknown tools are ignored", () => {
  assert.deepEqual(buildActivity([note("   "), started("mystery_tool"), finished("mystery_tool")]), []);
});
