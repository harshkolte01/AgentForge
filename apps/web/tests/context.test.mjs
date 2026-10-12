import assert from "node:assert/strict";
import { test } from "node:test";
import { coverageText, estimateDraftTokens, formatTokens, projectContext } from "../lib/context.ts";

const status = {
  model: "gpt-6-luna", provider: "openai", window_tokens: 1_050_000,
  input_limit_tokens: 922_000, max_output_tokens: 8192,
  baseline_tokens: 6000, history_tokens: 2000, history_budget_tokens: 12_000,
  used_tokens: 90_000, used_basis: "last_run_peak", level: "ok", percent: 9,
  messages_total: 4, messages_in_full: 3, messages_condensed: 1, messages_omitted: 0,
  last_run: { peak_input_tokens: 90_000, model_calls: 4, compacted_results: 0 },
};

test("a draft belongs to the next message, never to a historical peak", () => {
  const projected = projectContext(status, 1000);
  assert.equal(projected.used, 9000);
  assert.equal(projected.percent, 1);
  assert.equal(projected.level, "ok");
  assert.equal(status.last_run.peak_input_tokens, 90_000);
});

test("new empty conversation shows no sent context; typing includes the instruction budget", () => {
  const empty = { ...status, messages_total: 0, history_tokens: 0, last_run: null };
  assert.deepEqual(projectContext(empty, 0), { used: 0, percent: 0, level: "ok", empty: true, basis: "estimated" });
  assert.equal(projectContext(empty, 100).used, 6100);
});

test("unknown capacity is distinct from empty or healthy", () => {
  const unknown = projectContext({ ...status, window_tokens: null, input_limit_tokens: null }, 1000);
  assert.equal(unknown.level, "unknown");
  assert.equal(unknown.used, 9000);
});

test("older API responses reserve reply space and oversized drafts flag input capacity", () => {
  const old = { ...status, input_limit_tokens: undefined, window_tokens: 10_000, max_output_tokens: 2000 };
  assert.equal(projectContext(old, 0).level, "high");
  assert.equal(projectContext(status, 1_000_000).percent, 100);
});

test("Unicode code points and attachments are included in the draft estimate", () => {
  assert.equal(estimateDraftTokens("界🙂", []), 4);
  assert.equal(estimateDraftTokens("abc", [{ name: "a.md", content: "界🙂" }]), 7);
  assert.equal(estimateDraftTokens("", []), 0);
});

test("model window formatting keeps the 1.05M distinction and coverage discloses shortened text", () => {
  assert.equal(formatTokens(1_050_000), "1.05M");
  assert.equal(formatTokens(922_000), "922K");
  assert.match(coverageText(status), /3 in full, 1 condensed/);
  assert.doesNotMatch(coverageText(status), /all read in full/);
});

const running = (patch = {}) => ({ ...status, last_run: { peak_input_tokens: 139_172, model_calls: 44, compacted_results: 0, latest_input_tokens: 139_172, first_input_tokens: 36_773, active: true, ...patch } });

test("while a run is going on the ring shows what the model actually holds, measured, and not the draft", () => {
  const live = projectContext(running(), 5000);
  assert.deepEqual(live, { used: 139_172, percent: 15.1, level: "ok", empty: false, basis: "measured" });
  assert.equal(projectContext(running({ latest_input_tokens: 800_000 }), 0).level, "high");
  assert.equal(projectContext(running({ latest_input_tokens: 1_500_000 }), 0).percent, 100);
});

test("once the run is over the ring goes back to the next message's estimate, and the measured run only informs", () => {
  const idle = projectContext(running({ active: false }), 1000);
  assert.equal(idle.basis, "estimated");
  assert.equal(idle.used, 9000);
});

test("a run that has not made a request yet, or a server that cannot measure, falls back to the estimate", () => {
  assert.equal(projectContext(running({ latest_input_tokens: null }), 0).basis, "estimated");
  assert.equal(projectContext(running({ latest_input_tokens: undefined, active: undefined }), 0).basis, "estimated");
  assert.equal(projectContext({ ...status, last_run: null }, 0).basis, "estimated");
});

test("measured usage with no configured limit is still shown, as unknown level", () => {
  const live = projectContext({ ...running(), window_tokens: null, input_limit_tokens: null }, 0);
  assert.deepEqual([live.used, live.level, live.basis], [139_172, "unknown", "measured"]);
});
