import assert from "node:assert/strict";
import { test } from "node:test";
import {
  RECONNECT_ATTEMPTS, activityAnnouncement, activityLabel, currentCallId, endEntries, formatDuration, heldAsLive, holdLive, runDuration, sectionsOf, summarizeEntries, syncNotice,
} from "../features/sessions/timeline-model.ts";
import { createActivityState, reduceActivity, selectEntries } from "../lib/activity/reducer.ts";
import { builder, researchRun } from "./fixtures/streams.mjs";
import { deepFreeze } from "./helpers/deep-freeze.mjs";

const entriesOf = (events) => selectEntries(reduceActivity(createActivityState("task-1"), { type: "events", events }));
const sync = (patch = {}) => ({ phase: "live", loading: false, attempts: 0, error: null, halted: false, awaitingAnswer: false, ...patch });

// Connection notices ---------------------------------------------------------------------------------------------------

test("a healthy connection says nothing, and so does the normal first load", () => {
  assert.equal(syncNotice(sync()), null);
  assert.equal(syncNotice(sync({ phase: "connecting" })), null);
  assert.equal(syncNotice(sync({ phase: "complete" })), null);
});

test("reading new events while following a run is routine and shows no line (so \"Thinking\" never flips to \"Catching up…\")", () => {
  assert.equal(syncNotice(sync({ phase: "catching_up", loading: true })), null);
  assert.equal(syncNotice(sync({ phase: "catching_up", loading: false, awaitingAnswer: true })), null);
  assert.equal(syncNotice(sync({ phase: "catching_up", loading: true, awaitingAnswer: true })), null);
});

test("the first failures read as Reconnecting; only repeated failure asks for a manual retry", () => {
  for (const phase of ["retrying", "unreachable"]) {
    assert.equal(syncNotice(sync({ phase, attempts: 1, error: { kind: "network" } })).kind, "reconnecting");
    assert.equal(syncNotice(sync({ phase, attempts: RECONNECT_ATTEMPTS - 1, error: { kind: "network" } })).kind, "reconnecting");
    assert.deepEqual(syncNotice(sync({ phase, attempts: RECONNECT_ATTEMPTS, error: { kind: "network" } })), { kind: "failed", text: "Couldn't load the latest activity" });
  }
});

test("a server that cannot yet give every event reads as catching up, then as failed", () => {
  assert.equal(syncNotice(sync({ phase: "retrying", attempts: 1, error: { kind: "incomplete" } })).kind, "catching_up");
  assert.equal(syncNotice(sync({ phase: "retrying", attempts: 4, error: { kind: "incomplete" } })).kind, "failed");
});

test("a halted controller explains why and never offers a retry", () => {
  assert.match(syncNotice(sync({ halted: true, phase: "unreachable", error: { kind: "revoked" } })).text, /Sign in again/);
  assert.match(syncNotice(sync({ halted: true, phase: "unreachable", error: { kind: "unauthorized" } })).text, /Sign in again/);
  assert.match(syncNotice(sync({ halted: true, phase: "unreachable", error: { kind: "not_found" } })).text, /no longer available/);
  assert.equal(syncNotice(sync({ halted: true, phase: "unreachable", error: { kind: "revoked" } })).kind, "halted");
});

// Announcements ---------------------------------------------------------------------------------------------------------

test("announcements are short state changes, never streamed words", () => {
  const base = { notice: null, ended: null, stopping: false, actions: 0 };
  assert.equal(activityAnnouncement(base), "Working");
  assert.equal(activityAnnouncement({ ...base, actions: 1 }), "Working, 1 action");
  assert.equal(activityAnnouncement({ ...base, actions: 7 }), "Working, 7 actions");
  assert.equal(activityAnnouncement({ ...base, stopping: true, actions: 7 }), "Stopping");
  assert.equal(activityAnnouncement({ ...base, notice: { kind: "reconnecting", text: "Reconnecting…" } }), "Reconnecting");
  assert.equal(activityAnnouncement({ ...base, notice: { kind: "failed", text: "Couldn't load the latest activity" } }), "Couldn't load the latest activity");
  assert.equal(activityAnnouncement({ ...base, notice: { kind: "catching_up", text: "Catching up…" }, actions: 2 }), "Working, 2 actions", "a brief catch-up is not announced");
});

test("the end of a run is announced by its status and beats everything else", () => {
  const base = { notice: { kind: "failed", text: "x" }, stopping: true, actions: 3 };
  assert.equal(activityAnnouncement({ ...base, ended: "COMPLETED" }), "Finished");
  assert.equal(activityAnnouncement({ ...base, ended: "PARTIAL" }), "Finished with partial results");
  assert.equal(activityAnnouncement({ ...base, ended: "CANCELLED" }), "Stopped");
  assert.equal(activityAnnouncement({ ...base, ended: "FAILED" }), "Couldn't finish");
  assert.equal(activityAnnouncement({ ...base, ended: "SOMETHING_ELSE" }), "Finished");
});

test("the disclosure label reads 'Worked · duration · N steps' and leaves out what it does not know", () => {
  assert.equal(activityLabel(22, 41_000), "Worked · 41s · 22 steps");
  assert.equal(activityLabel(1, 41_000), "Worked · 41s · 1 step");
  assert.equal(activityLabel(0, 41_000), "Worked · 41s");
  assert.equal(activityLabel(22), "Worked · 22 steps");
  assert.equal(activityLabel(22, null), "Worked · 22 steps");
  assert.equal(activityLabel(0), "Worked");
});

test("durations read as seconds, minutes and hours", () => {
  assert.equal(formatDuration(200), "1s");
  assert.equal(formatDuration(41_000), "41s");
  assert.equal(formatDuration(124_000), "2m 4s");
  assert.equal(formatDuration(3_780_000), "1h 3m");
});

test("a run's duration spans its first to last event and is unknown without two timestamps", () => {
  const at = (created_at) => ({ created_at });
  assert.equal(runDuration([at("2026-01-01T00:00:10Z"), at("2026-01-01T00:00:51Z"), at("2026-01-01T00:00:30Z")]), 41_000);
  assert.equal(runDuration([at("2026-01-01T00:00:10Z")]), null);
  assert.equal(runDuration([at("nope"), at("2026-01-01T00:00:10Z")]), null);
  assert.equal(runDuration([]), null);
});

// Entries ---------------------------------------------------------------------------------------------------------------

test("summarizeEntries counts calls inside groups and notes, nothing else", () => {
  const entries = entriesOf(researchRun({ style: "modern" }));
  const { actions, notes } = summarizeEntries(entries);
  assert.ok(entries.some((entry) => entry.kind === "group"), "the fixture folds reads into a group");
  assert.equal(notes, entries.filter((entry) => entry.kind === "note").length);
  const direct = entries.reduce((total, entry) => total + (entry.kind === "call" ? 1 : entry.kind === "group" ? entry.calls.length : 0), 0);
  assert.equal(actions, direct);
});

test("the current call is the newest running one; none when nothing runs", () => {
  const b = builder({ style: "modern" });
  const first = b.start("web_search", "a");
  const second = b.start("web_search", "b");
  const running = entriesOf(b.events);
  assert.equal(currentCallId(running), `call:${second.tool_call_id}`);
  b.finish(second);
  assert.equal(currentCallId(entriesOf(b.events)), `call:${first.tool_call_id}`);
  b.finish(first);
  assert.equal(currentCallId(entriesOf(b.events)), null);
});

test("notes and headings split step lists; consecutive steps share one", () => {
  const b = builder({ style: "modern" });
  b.phase("builtin/verifier", "s1");
  b.call("web_search", "a");
  b.call("fetch_webpage", "b");
  b.note("Now reading.", 1);
  b.call("web_search", "c");
  const sections = sectionsOf(entriesOf(b.events));
  assert.deepEqual(sections.map((section) => [section.kind, section.kind === "rows" ? section.entries.length : 1]), [["phase", 1], ["rows", 2], ["note", 1], ["rows", 1]]);
  assert.equal(new Set(sections.map((section) => section.id)).size, sections.length, "section ids are unique and usable as keys");
});

test("endEntries turns a running call into an unknown one and keeps everything else by identity", () => {
  const b = builder({ style: "modern" });
  b.phase("builtin/verifier", "s1");
  b.call("web_search", "done");
  b.start("run_tests", "node_test");
  const entries = deepFreeze(entriesOf(b.events));
  const ended = endEntries(entries);
  const settled = ended.filter((entry) => entry.kind === "call");
  assert.deepEqual(settled.map((entry) => entry.outcome), ["completed", "unknown"]);
  assert.equal(settled[1].icon, "unknown");
  assert.match(settled[1].label, /may not have finished/);
  assert.equal(settled[1].detailable, true, "an interrupted execution call can still be asked about");
  assert.equal(ended.find((entry) => entry.kind === "phase").state, "done");
  assert.equal(ended[1], entries[1], "settled rows keep their identity");
  assert.equal(endEntries(entries)[2], ended[2], "the projection of a running row is cached, so memoized rows do not re-render");
  assert.equal(currentCallId(ended), null, "nothing is left spinning");
});

test("endEntries returns the very same array when nothing is running", () => {
  const b = builder({ style: "modern" });
  b.call("web_search", "x");
  const entries = entriesOf(b.events);
  assert.equal(endEntries(entries), entries);
});

test("endEntries does not leave the running row's detail wording behind", () => {
  const b = builder({ style: "modern" });
  b.start("web_search", "agent harness");
  const [call] = endEntries(entriesOf(b.events));
  assert.equal(call.outcome, "unknown");
  assert.equal(call.detail, "“agent harness”", "the query is still what the call was about");
  assert.equal(call.rawDetail, "agent harness");
});

// Live text held through the end of a run ---------------------------------------------------------------------------------

const live = (text, epoch = 2, kind = "answer") => ({ epoch, kind, text });

test("live text is held while it grows and keeps its identity when nothing changed", () => {
  const first = holdLive(null, live("Hel"), false);
  assert.deepEqual(first, { epoch: 2, kind: "answer", text: "Hel" });
  assert.equal(holdLive(first, live("Hel"), false), first);
  assert.deepEqual(holdLive(first, live("Hello"), false), { epoch: 2, kind: "answer", text: "Hello" });
});

test("text cleared while the run is going is a discard: nothing is held", () => {
  const held = holdLive(null, live("draft that gets thrown away"), false);
  assert.equal(holdLive(held, live(""), false), null);
});

test("text cleared at the end of the run is kept until the saved answer takes over (no blank frame)", () => {
  const held = holdLive(null, live("The whole answer"), false);
  assert.equal(holdLive(held, live(""), true), held);
  assert.equal(holdLive(null, live(""), true), null, "nothing was ever shown, nothing to hold");
  assert.deepEqual(heldAsLive(held), { epoch: 2, revision: 0, kind: "answer", text: "The whole answer", length: 16 });
});

test("a newer segment replaces what is held even after the end", () => {
  const held = holdLive(null, live("old", 1, "progress"), false);
  assert.deepEqual(holdLive(held, live("new", 2), true), { epoch: 2, kind: "answer", text: "new" });
});
