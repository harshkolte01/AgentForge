import { test } from "node:test";
import assert from "node:assert/strict";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { categoriesOf, groupByCategory, matchesEntry, signInLabel, signInOf, tokenInstead } from "../lib/plugin-catalog.ts";
import { pluginLogos } from "../lib/plugin-logos.ts";
import { oauthMessage } from "../lib/plugins-api.ts";
import { oauthResult } from "../features/plugins/plugins-tabs.ts";

const here = path.dirname(fileURLToPath(import.meta.url));
const entry = (key, category, extra = {}) => ({ key, name: key.toUpperCase(), description: `About ${key}`, category, server_url: "https://x.example/mcp", auth: "none", oauth: false, token_label: "Access token", token_help: "", docs_url: null, ...extra });

test("categories are shown in the catalog's order, with strangers last and alphabetical", () => {
  const entries = [entry("a", "Search"), entry("b", "Code"), entry("c", "Zebra"), entry("d", "Cloud"), entry("e", "Aardvark"), entry("f", "Code")];
  assert.deepEqual(categoriesOf(entries), ["Code", "Cloud", "Search", "Aardvark", "Zebra"]);
  const groups = groupByCategory(entries);
  assert.deepEqual(groups.map((group) => group.category), ["Code", "Cloud", "Search", "Aardvark", "Zebra"]);
  assert.deepEqual(groups[0].entries.map((item) => item.key), ["b", "f"], "a group keeps the catalog's own order");
  assert.deepEqual(categoriesOf([]), []);
});

test("search matches a name, key, category or description, ignoring case and spacing", () => {
  const linear = entry("linear", "Code", { name: "Linear", description: "Find and update issues" });
  for (const query of ["", "  ", "lin", "LINEAR", "code", "UPDATE  issues", "  issues "]) assert.ok(matchesEntry(linear, query), query);
  for (const query of ["notion", "zzz", "linearx"]) assert.ok(!matchesEntry(linear, query), query);
});

test("how a plugin signs in follows what the catalog says", () => {
  assert.equal(signInOf({ auth: "none", oauth: true }), "oauth");
  assert.equal(signInOf({ auth: "optional", oauth: true }), "oauth-or-token");
  assert.equal(signInOf({ auth: "required", oauth: false }), "token");
  assert.equal(signInOf({ auth: "optional", oauth: false }), "token-optional");
  assert.equal(signInOf({ auth: "none", oauth: false }), "open");
  const label = (auth, oauth) => signInLabel({ auth, oauth, token_label: "API key" });
  assert.equal(label("none", true), "Sign in with your account");
  assert.equal(label("optional", true), "Sign in, or use an API key");
  assert.equal(label("required", false), "Needs an API key");
  assert.equal(label("optional", false), "Works without an API key");
  assert.equal(label("none", false), "No account needed");
  assert.equal(signInLabel({ auth: "required", oauth: false, token_label: "Personal access token" }), "Needs a personal access token");
  assert.equal(signInLabel({ auth: "optional", oauth: true, token_label: "Access token" }), "Sign in, or use an access token");
  assert.equal(tokenInstead({ token_label: "API key" }), "Use an API key instead");
});

test("a sign-in's result is read from the page's query, and only what is expected", () => {
  assert.deepEqual(oauthResult({ oauth: "connected", plugin: "Notion" }), { status: "connected", plugin: "Notion" });
  assert.deepEqual(oauthResult({ oauth: "connected" }), { status: "connected", plugin: "The plugin" });
  assert.deepEqual(oauthResult({ oauth: "connected", plugin: "x".repeat(500) }), { status: "connected", plugin: "The plugin" });
  assert.deepEqual(oauthResult({ oauth: "error", code: "plugin_oauth_denied" }), { status: "error", code: "plugin_oauth_denied" });
  assert.deepEqual(oauthResult({ oauth: "error", code: "<script>" }), { status: "error", code: "plugin_oauth_failed" }, "a code that is not plain is not used");
  assert.deepEqual(oauthResult({ oauth: "error" }), { status: "error", code: "plugin_oauth_failed" });
  for (const query of [{}, { oauth: "other" }, { oauth: ["connected", "error"] }, { tab: "skills" }]) assert.equal(oauthResult(query), null, JSON.stringify(query));
});

test("a person is told what happened in our words, never the server's", () => {
  assert.match(oauthMessage("plugin_oauth_denied"), /cancelled/);
  assert.match(oauthMessage("plugin_oauth_expired"), /Start it again/);
  assert.match(oauthMessage("something_new"), /could not be completed/);
  assert.match(oauthMessage(undefined), /could not be completed/);
  assert.match(oauthMessage("plugin_already_connected"), /already connected/);
});

test("every plugin in the catalog has a logo, and every logo is a file that exists", () => {
  const source = readFileSync(path.join(here, "../../api/src/agentforge/plugins/catalog.py"), "utf8");
  const keys = [...source.matchAll(/(?:_oauth|_open|CatalogPlugin)\("([a-z0-9-]+)"/g)].map((match) => match[1]);
  assert.ok(keys.length >= 40, `found ${keys.length} catalog entries`);
  assert.equal(new Set(keys).size, keys.length, "catalog keys are unique");
  const missing = keys.filter((key) => !(key in pluginLogos));
  assert.deepEqual(missing, [], "plugins without a logo (run `node scripts/plugin-logos.mjs`)");
  assert.deepEqual(Object.keys(pluginLogos).filter((key) => !keys.includes(key)), [], "logos for plugins that are no longer in the catalog");
  const folder = path.join(here, "../public/plugins");
  for (const [key, logo] of Object.entries(pluginLogos)) {
    assert.ok(existsSync(path.join(folder, logo.file)), `${key}: ${logo.file} is missing`);
    assert.ok(logo.fit === "glyph" || logo.fit === "icon", key);
    if (logo.dark) assert.ok(logo.file.endsWith(".svg") && logo.fit === "glyph", `${key}: only a drawn mark can be shown white`);
  }
  assert.deepEqual(readdirSync(folder).filter((file) => !Object.values(pluginLogos).some((logo) => logo.file === file)), [], "files no logo uses");
});

test("the logo files are plain pictures: no script, no outside references, and small", () => {
  const folder = path.join(here, "../public/plugins");
  for (const file of readdirSync(folder)) {
    const bytes = readFileSync(path.join(folder, file));
    assert.ok(bytes.length < 60_000, `${file} is ${bytes.length} bytes`);
    if (file.endsWith(".svg")) {
      const text = bytes.toString("utf8");
      assert.match(text, /^<svg[\s>]/, `${file} is not an SVG`);
      assert.doesNotMatch(text, /<script|javascript:|\son\w+=|<foreignObject|<image|xlink:href=["']https?:|@import|url\(\s*["']?https?:/i, `${file} carries something it should not`);
    } else {
      assert.equal(bytes.subarray(1, 4).toString(), "PNG", `${file} is not a PNG`);
    }
  }
});
