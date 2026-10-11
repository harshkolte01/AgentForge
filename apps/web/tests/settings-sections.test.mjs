import { test } from "node:test";
import assert from "node:assert/strict";
import { settingsSection, settingsSections } from "../features/settings/settings-sections.ts";

test("every listed section maps to itself", () => {
  for (const { id } of settingsSections) assert.equal(settingsSection(id), id);
});

test("retired Status and runtime links open Account", () => {
  assert.equal(settingsSection("runtime"), "account");
  assert.equal(settingsSection("status"), "account");
  assert.ok(settingsSections.every(({ id }) => id !== "status"));
});

test("missing, unknown and repeated values open Account", () => {
  assert.equal(settingsSection(undefined), "account");
  assert.equal(settingsSection("billing"), "account");
  assert.equal(settingsSection(["github", "agents"]), "account");
});

test("custom agents are listed last and marked Labs", () => {
  const last = settingsSections.at(-1);
  assert.equal(last.id, "agents");
  assert.equal(last.labs, true);
});
