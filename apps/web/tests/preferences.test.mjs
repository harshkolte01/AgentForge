import { test } from "node:test";
import assert from "node:assert/strict";
import { parseEnterSends, parseTheme, resolveDark, THEME_KEY, themeScript } from "../lib/preferences.ts";

test("stored values are read defensively", () => {
  assert.equal(parseTheme(null), "system");
  assert.equal(parseTheme("dark"), "dark");
  assert.equal(parseTheme("light"), "light");
  assert.equal(parseTheme("purple"), "system");
  assert.equal(parseEnterSends(null), true);
  assert.equal(parseEnterSends("false"), false);
  assert.equal(parseEnterSends("anything"), true);
});

test("System follows the operating system; an explicit choice wins", () => {
  assert.equal(resolveDark("system", true), true);
  assert.equal(resolveDark("system", false), false);
  assert.equal(resolveDark("light", true), false);
  assert.equal(resolveDark("dark", false), true);
});

/** Runs the inline head script against a fake page and returns what it set, plus a way to flip the system theme. */
function runScript({ stored = null, systemDark = false, storageThrows = false } = {}) {
  const classes = new Set();
  const listeners = {};
  const media = { matches: systemDark, addEventListener: (type, callback) => { listeners.media = callback; } };
  const element = { classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)) }, style: {} };
  const window = { matchMedia: () => media, addEventListener: (type, callback) => { listeners[type] = callback; } };
  const localStorage = { getItem: (key) => { if (storageThrows) throw new Error("blocked"); return key === THEME_KEY ? stored : null; } };
  new Function("window", "document", "localStorage", themeScript)(window, { documentElement: element }, localStorage);
  return { dark: () => classes.has("dark"), scheme: () => element.style.colorScheme, system(dark) { media.matches = dark; listeners.media(); }, store(value) { stored = value; listeners.storage({ key: THEME_KEY }); } };
}

test("the head script applies the theme before paint and keeps System live", () => {
  const page = runScript({ systemDark: true });
  assert.equal(page.dark(), true);
  assert.equal(page.scheme(), "dark");
  page.system(false);
  assert.equal(page.dark(), false);
  assert.equal(page.scheme(), "light");
  page.store("dark");
  assert.equal(page.dark(), true, "a choice made in another tab applies");
  page.system(false);
  assert.equal(page.dark(), true, "an explicit choice ignores the system");
  assert.equal(runScript({ stored: "light", systemDark: true }).dark(), false);
  assert.equal(runScript({ storageThrows: true, systemDark: true }).dark(), true, "blocked storage falls back to System");
});
