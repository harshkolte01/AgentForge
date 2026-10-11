import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { faqs, sections, steps } from "../features/landing/content.ts";

const source = (file) => readFileSync(new URL(`../${file}`, import.meta.url), "utf8");

test("public navigation only links to sections rendered on the page", () => {
  const page = source("app/page.tsx");
  const components = { "how-it-works": "HowItWorks", faq: "Faq", possibilities: "Possibilities" };
  assert.equal(new Set(sections.map(({ id }) => id)).size, sections.length);
  for (const { id } of sections) {
    assert.match(page, new RegExp(`<${components[id]} />`));
    assert.match(source(`features/landing/${id}.tsx`), new RegExp(`<(?:Section|section) id="${id}"`));
  }
});

test("public copy explains the product without internal implementation details", () => {
  const copy = JSON.stringify({ steps, faqs });
  assert.ok(steps.length && faqs.length);
  assert.equal(new Set(faqs.map(({ q }) => q)).size, faqs.length);
  assert.doesNotMatch(copy, /backend|credentials|pinned commit|provider|server.side|bootstrap|lorem|placeholder/i);
  assert.match(copy, /Ask me/);
  assert.match(copy, /Approve for me/);
});

test("authentication has no navigation header or operator setup commands", () => {
  assert.doesNotMatch(source("features/auth/auth-shell.tsx"), /<header|<nav/);
  assert.doesNotMatch(source("features/auth/auth-panel.tsx"), /LOCAL_BOOTSTRAP_TOKEN|--write-env|\{state.error\}/);
});
