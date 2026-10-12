import assert from "node:assert/strict";
import { test } from "node:test";
import { approvalTitle } from "../lib/activity/approvals.ts";
import { describeCall, isActionTool, pluginName } from "../lib/activity/presentation.ts";
import { defaultSelection, effectiveSelection, serverHost, toggled, toolSummary } from "../lib/plugins-api.ts";
import { createPluginsStore } from "../lib/plugins-store.ts";
import { settingsSection, settingsSections } from "../features/settings/settings-sections.ts";

const tool = (name, read_only = true, enabled = true) => ({ name, title: "", description: "", read_only, enabled });
const connection = (id, extra = {}) => ({
  id, plugin_key: "custom", name: `Plugin ${id}`, server_url: "https://mcp.example.com/mcp?x=1", description: null, auth: "none", has_credential: false,
  enabled: true, status: "connected", last_error: null, server_name: null, tools: [tool("a"), tool("b", false)], tool_count: 2, enabled_tool_count: 2,
  tools_synced_at: null, created_at: null, ...extra,
});
const overview = (connections) => ({ catalog: [], connections, enabled: true, credentials: true, max_connections: 20 });

test("a new conversation starts with the plugins left on, in the order they were connected", () => {
  const all = [connection("1"), connection("2", { enabled: false }), connection("3")];
  assert.deepEqual(defaultSelection(all), ["1", "3"]);
  assert.deepEqual(effectiveSelection(all, null), ["1", "3"]);
  assert.deepEqual(effectiveSelection(all, undefined), ["1", "3"]);
});

test("a conversation's own choice is kept exactly, minus plugins that were removed", () => {
  const all = [connection("1"), connection("2")];
  assert.deepEqual(effectiveSelection(all, ["2"]), ["2"]);
  assert.deepEqual(effectiveSelection(all, []), []);
  assert.deepEqual(effectiveSelection(all, ["2", "gone"]), ["2"]);
});

test("switching a plugin on or off keeps connection order and never duplicates", () => {
  const all = [connection("1"), connection("2"), connection("3")];
  assert.deepEqual(toggled(all, ["3"], "1", true), ["1", "3"]);
  assert.deepEqual(toggled(all, ["1", "3"], "1", true), ["1", "3"]);
  assert.deepEqual(toggled(all, ["1", "3"], "3", false), ["1"]);
  assert.deepEqual(toggled(all, [], "2", false), []);
});

test("a server is named by its host, never its path or query", () => {
  assert.equal(serverHost("https://mcp.example.com/mcp?token=secret"), "mcp.example.com");
  assert.equal(serverHost("not a url"), "not a url");
});

test("the tool summary says how many tools, how many are off and how many can change things", () => {
  assert.equal(toolSummary(connection("1", { tools: [tool("a")], tool_count: 1, enabled_tool_count: 1 })), "1 tool");
  assert.equal(toolSummary(connection("1")), "2 tools · 1 can change things");
  assert.equal(toolSummary(connection("1", { tools: [tool("a"), tool("b", false, false), tool("c")], tool_count: 3, enabled_tool_count: 2 })), "2 of 3 tools on");
});

test("the store reads once, shares the answer and keeps changes made after a read began", async () => {
  let reads = 0;
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  const store = createPluginsStore({ fetch: async () => { reads += 1; await gate; return overview([connection("1")]); }, describe: String });
  const seen = [];
  store.subscribe(() => seen.push(store.getSnapshot().status));
  store.load(); store.load();
  assert.equal(store.getSnapshot().status, "loading");
  release();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(reads, 1);
  assert.equal(store.getSnapshot().status, "ready");
  store.load();
  assert.equal(reads, 1, "a known overview is not read again by load()");
  store.apply(connection("2"));
  store.apply(connection("1", { name: "Renamed" }));
  assert.deepEqual(store.getSnapshot().overview.connections.map((item) => item.name), ["Renamed", "Plugin 2"]);
  store.remove("1");
  assert.deepEqual(store.getSnapshot().overview.connections.map((item) => item.id), ["2"]);
  store.reset();
  assert.deepEqual(store.getSnapshot(), { status: "idle", overview: null, error: "" });
});

test("a read that was overtaken by a change does not bring the old list back", async () => {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  let first = true;
  const store = createPluginsStore({ fetch: async () => { if (first) { first = false; return overview([connection("1")]); } await gate; return overview([connection("1")]); }, describe: String });
  store.load();
  await new Promise((resolve) => setTimeout(resolve, 0));
  store.reload();
  store.apply(connection("2"));
  release();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(store.getSnapshot().overview.connections.map((item) => item.id), ["1", "2"]);
});

test("a failed read says why, keeps what was known, and load() tries again", async () => {
  let fail = true;
  const store = createPluginsStore({ fetch: async () => { if (fail) throw new Error("down"); return overview([]); }, describe: (failure) => `Could not read: ${failure.message}` });
  store.load();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(store.getSnapshot().status, "error");
  assert.equal(store.getSnapshot().error, "Could not read: down");
  fail = false;
  store.load();
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(store.getSnapshot().status, "ready");
  assert.equal(store.getSnapshot().error, "");
});

test("a plugin's tools read as the plugin, not as a raw tool name", () => {
  assert.equal(pluginName("linear__create_issue"), "Linear");
  assert.equal(pluginName("my-server__run"), "My server");
  assert.equal(pluginName("bash"), null);
  assert.equal(pluginName("__x"), null);
  assert.equal(pluginName("a__"), null);
  const running = describeCall({ tool: "linear__create_issue", outcome: "running", rawDetail: "Create issue" });
  assert.equal(running.label, "Using Linear");
  assert.equal(running.detail, "Create issue");
  assert.equal(describeCall({ tool: "linear__create_issue", outcome: "completed" }).label, "Used Linear");
  assert.equal(describeCall({ tool: "linear__create_issue", outcome: "failed" }).label, "Couldn't use Linear");
  assert.equal(describeCall({ tool: "linear__create_issue", outcome: "blocked" }).label, "Couldn't use Linear: not allowed");
  assert.equal(isActionTool("linear__create_issue"), true);
});

test("approving a plugin's action names the plugin", () => {
  assert.equal(approvalTitle("linear__create_issue"), "Allow Linear to do this?");
  assert.equal(approvalTitle("bash"), "Run this command?");
  assert.equal(approvalTitle("something"), "Allow this action?");
});

test("Plugins lives on its own page, not in Settings", () => {
  assert.ok(!settingsSections.some((item) => item.id === "plugins"));
  assert.equal(settingsSection("plugins"), "account");
});
