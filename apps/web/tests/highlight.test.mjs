import assert from "node:assert/strict";
import { test } from "node:test";
import { HIGHLIGHT_MAX_CHARS, canonicalLanguage, highlightEligibility, highlightLanguages } from "../features/sessions/highlight-policy.ts";
import { highlightCode, parseHighlighted, tokenClass } from "../features/sessions/highlight.ts";

const flat = (nodes) => nodes.map((node) => typeof node === "string" ? node : flat(node.children)).join("");

test("language labels map to a bundled grammar, case-insensitively and through common aliases", () => {
  assert.equal(canonicalLanguage("TSX"), "typescript");
  assert.equal(canonicalLanguage("js"), "javascript");
  assert.equal(canonicalLanguage("sh"), "bash");
  assert.equal(canonicalLanguage("c++"), "cpp");
  assert.equal(canonicalLanguage("Python"), "python");
  assert.equal(canonicalLanguage("html"), "xml");
  for (const language of highlightLanguages) assert.equal(canonicalLanguage(language), language);
  for (const unknown of ["brainfuck", "", "  ", null, undefined, "constructor", "__proto__"]) assert.equal(canonicalLanguage(unknown), null, String(unknown));
});

test("only completed, small, supported blocks are eligible", () => {
  const code = "const x = 1;";
  assert.deepEqual(highlightEligibility({ code, language: "ts", streaming: false }), { ok: true, language: "typescript" });
  assert.deepEqual(highlightEligibility({ code, language: "ts", streaming: true }), { ok: false, reason: "streaming" });
  assert.deepEqual(highlightEligibility({ code: "  \n", language: "ts", streaming: false }), { ok: false, reason: "empty" });
  assert.deepEqual(highlightEligibility({ code, language: "cobol", streaming: false }), { ok: false, reason: "unsupported" });
  assert.deepEqual(highlightEligibility({ code, language: undefined, streaming: false }), { ok: false, reason: "unsupported" });
  assert.deepEqual(highlightEligibility({ code: "x".repeat(HIGHLIGHT_MAX_CHARS + 1), language: "ts", streaming: false }), { ok: false, reason: "too_large" });
  assert.equal(highlightEligibility({ code: "x".repeat(HIGHLIGHT_MAX_CHARS), language: "ts", streaming: false }).ok, true);
});

test("the parser builds a tree from spans and decodes entities, and refuses anything else", () => {
  const nodes = parseHighlighted('<span class="hljs-keyword">if</span> (a &lt; b &amp;&amp; c &gt; &quot;d&quot;) <span class="hljs-string">&#x27;x&#x27;<span class="hljs-subst">${y}</span></span>');
  assert.deepEqual(nodes, [
    { className: "hljs-keyword", children: ["if"] }, ' (a < b && c > "d") ',
    { className: "hljs-string", children: ["'x'", { className: "hljs-subst", children: ["${y}"] }] },
  ]);
  assert.equal(parseHighlighted('<img src=x onerror="alert(1)">'), null);
  assert.equal(parseHighlighted('<a href="x">y</a>'), null);
  assert.equal(parseHighlighted('<span class="a">unclosed'), null);
  assert.equal(parseHighlighted("stray</span>"), null);
  assert.deepEqual(parseHighlighted(""), []);
  assert.deepEqual(parseHighlighted("plain"), ["plain"]);
});

test("highlighting keeps the code's text exactly, per language, and HTML in the code stays text", async () => {
  const samples = {
    javascript: 'const a = "x"; // note\nfunction f(n) { return n < 3 && <b>; }',
    typescript: "type A = { a: number }; export const f = (a: A): number => a.a;",
    python: 'def f(x):\n    """doc"""\n    return x if x else None  # c',
    bash: 'echo "$HOME" | grep -v x && ls -la',
    json: '{"a": [1, 2.5, true, null], "b": "<script>alert(1)</script>"}',
    xml: '<div class="a"><script>alert(1)</script></div>',
    css: "a:hover > b { color: #fff; margin: 0 auto; }",
    sql: "SELECT a, COUNT(*) FROM t WHERE b = 'x' GROUP BY a;",
    yaml: "a:\n  - b: 1\n  - c: 'x'",
    diff: "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new",
  };
  for (const [language, code] of Object.entries(samples)) {
    const nodes = await highlightCode(code, language);
    assert.ok(nodes, language);
    assert.equal(flat(nodes), code, `${language} text is unchanged`);
    assert.ok(nodes.some((node) => typeof node !== "string"), `${language} has at least one token`);
  }
});

test("a repeated block is answered from memory, and an empty block does not fail", async () => {
  const a = await highlightCode("let x = 1;", "javascript");
  const b = await highlightCode("let x = 1;", "javascript");
  assert.equal(a, b);
  assert.deepEqual(await highlightCode("", "python"), []);
});

test("token classes: coloured scopes get utilities (with a dark variant), others none; compound scopes use the main one", () => {
  assert.match(tokenClass("hljs-keyword"), /text-violet-700 dark:text-violet-300/);
  assert.match(tokenClass("hljs-title function_"), /text-sky-700/);
  assert.match(tokenClass("hljs-comment"), /italic/);
  assert.equal(tokenClass("hljs-punctuation"), "");
  assert.equal(tokenClass("language_"), "");
  assert.equal(tokenClass(""), "");
});
