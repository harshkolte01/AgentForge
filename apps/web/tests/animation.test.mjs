// Pins the entrance and thinking animations that make up AgentForge's visual identity. Changing these on purpose is a
// design decision, not a side effect of a refactor. The reduced-motion block used to re-declare the shimmer animation (so it
// never stopped); it is asserted below.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const css = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");
// Everything before the reduced-motion media query: the base rules.
const base = css.slice(0, css.indexOf("@media (prefers-reduced-motion: reduce)"));
const squash = (text) => text.replace(/\s+/g, " ").trim();

function rule(selector) {
  const match = new RegExp(`(?:^|\\n)${selector.replace(".", "\\.")}\\s*\\{([^}]*)\\}`).exec(base);
  assert.ok(match, `${selector} rule is missing`);
  return squash(match[1]);
}
function keyframes(name) {
  const match = new RegExp(`@keyframes ${name}\\s*\\{((?:[^{}]|\\{[^{}]*\\})*)\\}`).exec(base);
  assert.ok(match, `@keyframes ${name} is missing`);
  return squash(match[1]);
}

test("message-in: a quarter-second ease-out rise", () => {
  assert.match(rule(".message-in"), /animation: message-in 0\.24s ease-out both;/);
  assert.equal(keyframes("message-in"), "from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; }");
});

test("thinking-orb: a 1.6s ease-in-out pulse", () => {
  assert.match(rule(".thinking-orb"), /animation: thinking-orb 1\.6s ease-in-out infinite;/);
  assert.match(rule(".thinking-orb"), /width: 0\.5rem; height: 0\.5rem; border-radius: 9999px;/);
  assert.equal(keyframes("thinking-orb"), "0%, 100% { opacity: 0.35; transform: scale(0.7); } 50% { opacity: 1; transform: scale(1.05); }");
});

test("thinking-shimmer: a 1.7s linear light band across dimmed text", () => {
  const shimmer = rule(".thinking-shimmer");
  assert.match(shimmer, /animation: thinking-shimmer 1\.7s linear infinite;/);
  assert.match(shimmer, /background-image: linear-gradient\(100deg, var\(--shimmer-base\) 36%, var\(--shimmer-peak\) 50%, var\(--shimmer-base\) 64%\);/);
  assert.match(shimmer, /background-size: 250% 100%;/);
  assert.match(shimmer, /-webkit-text-fill-color: transparent;/);
  assert.equal(keyframes("thinking-shimmer"), "from { background-position: 100% 0; } to { background-position: 0% 0; }");
});

test("the reduced-motion fallback keeps the orb alive with a fade", () => {
  assert.match(keyframes("thinking-fade"), /opacity: 0\.3;.*opacity: 0\.85;/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)[\s\S]*\.thinking-orb \{ animation: thinking-fade 2s ease-in-out infinite; \}/);
});

test("reduced motion stops the shimmer and the entrance, and leaves readable text behind", () => {
  const media = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
  const shimmer = squash(/\.thinking-shimmer \{([^}]*)\}/.exec(media)?.[1] ?? "");
  assert.match(shimmer, /animation: none;/);
  assert.doesNotMatch(shimmer, /infinite/);
  assert.match(shimmer, /background-image: none;/);
  assert.match(shimmer, /-webkit-text-fill-color: currentColor;/, "text must not stay transparent once the gradient is gone");
  assert.match(media, /\.message-in \{ animation: none; \}/);
  assert.doesNotMatch(media, /thinking-shimmer 1\.7s/, "the base shimmer animation must not be re-declared");
});

test("normal-motion rules are untouched by the reduced-motion block (it sits after them, inside the media query only)", () => {
  assert.equal(css.indexOf("animation: thinking-shimmer 1.7s linear infinite;"), css.lastIndexOf("animation: thinking-shimmer 1.7s linear infinite;"));
});
