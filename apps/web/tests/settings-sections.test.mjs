import { test } from "node:test";
import assert from "node:assert/strict";
import { movedSection, settingsSection, settingsSections } from "../features/settings/settings-sections.ts";

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

test("Settings lists exactly Account, Agent, GitHub, Conversations, Appearance and Custom agents", () => {
  assert.deepEqual(settingsSections.map(({ id }) => id), ["account", "agent", "github", "conversations", "appearance", "agents"]);
});

test("old Plugins and Skills settings links are sent to the Plugins page, clean of the old query", () => {
  assert.equal(movedSection("plugins"), "/plugins");
  assert.equal(movedSection("skills"), "/plugins?tab=skills");
  // Everything else stays in Settings, repeated values included.
  for (const value of [undefined, "github", "account", "agents", "billing", ["plugins", "skills"], ""]) assert.equal(movedSection(value), null);
});
