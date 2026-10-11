import { test } from "node:test";
import assert from "node:assert/strict";
import { pluginsTab, pluginsTabs } from "../features/plugins/plugins-tabs.ts";
import { routes } from "../lib/routes.ts";

test("every listed tab maps to itself", () => {
  for (const { id } of pluginsTabs) assert.equal(pluginsTab(id), id);
});

test("missing, unknown and repeated values open Plugins", () => {
  assert.equal(pluginsTab(undefined), "plugins");
  assert.equal(pluginsTab("billing"), "plugins");
  assert.equal(pluginsTab(["skills", "plugins"]), "plugins");
});

test("Plugins comes first and Skills is the other tab", () => {
  assert.deepEqual(pluginsTabs.map((tab) => tab.id), ["plugins", "skills"]);
});

test("the Plugins page has its own URL, with the tab as a query", () => {
  assert.equal(routes.plugins(), "/plugins");
  assert.equal(routes.plugins("skills"), "/plugins?tab=skills");
});
