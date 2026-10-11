import { test } from "node:test";
import assert from "node:assert/strict";
import { checklistMode, modelConfigured, setupItems } from "../features/sessions/first-run-model.ts";

const ready = { research_configured: true, coding_configured: true, general_configured: true, tracing_configured: false, qualification: "", missing_settings: [], coding_missing_settings: [], general_missing_settings: [] };

test("a fully configured workspace (older server, no new fields) has nothing to set up", () => {
  assert.deepEqual(setupItems(ready), []);
  assert.equal(checklistMode([], false), "hidden");
});

test("no model is required, names the missing settings and opens the list", () => {
  const items = setupItems({ ...ready, general_configured: false, research_configured: false, coding_configured: false, general_missing_settings: ["LLM_API_KEY", "LLM_MODEL"] });
  assert.equal(modelConfigured({ ...ready, general_configured: false, research_configured: false, coding_configured: false }), false);
  assert.deepEqual(items.map((item) => [item.id, item.required]), [["model", true], ["sandbox", false]]);
  assert.match(items[0].detail, /LLM_API_KEY, LLM_MODEL/);
  assert.equal(checklistMode(items, false), "open");
});

test("an offline worker is required; an unknown worker is not reported", () => {
  assert.deepEqual(setupItems({ ...ready, worker: { status: "offline", last_seen_at: null } }).map((item) => item.id), ["worker"]);
  assert.deepEqual(setupItems({ ...ready, worker: { status: "online", last_seen_at: "2026-10-11T00:00:00Z" } }), []);
  assert.deepEqual(setupItems({ ...ready, worker: null }), []);
});

test("optional items collapse the list; dismissing hides it even when something is required", () => {
  const optional = setupItems({ ...ready, web_research_configured: false }, { configured: true, connected: false });
  assert.deepEqual(optional.map((item) => [item.id, item.required]), [["web", false], ["github", false]]);
  assert.equal(checklistMode(optional, false), "collapsed");
  assert.equal(checklistMode(setupItems({ ...ready, general_configured: false, research_configured: false, coding_configured: false }), true), "hidden");
});

test("GitHub is only suggested when the App is configured and not yet connected", () => {
  assert.deepEqual(setupItems(ready, { configured: false, connected: false }), []);
  assert.deepEqual(setupItems(ready, { configured: true, connected: true }), []);
  assert.deepEqual(setupItems(ready, null), []);
});
