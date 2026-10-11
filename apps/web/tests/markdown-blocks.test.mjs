import assert from "node:assert/strict";
import { test } from "node:test";
import { createElement, Fragment } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { splitMarkdownBlocks } from "../lib/markdown-blocks.ts";

const html = (text) => renderToStaticMarkup(createElement(Markdown, { remarkPlugins: [remarkGfm], skipHtml: true }, text));
/** Rendering the blocks one by one must give exactly the markup of rendering the whole text. */
function sameAsWhole(text) {
  const blocks = splitMarkdownBlocks(text);
  const pieces = renderToStaticMarkup(createElement(Fragment, null, ...blocks.map((block, index) => createElement(Markdown, { key: index, remarkPlugins: [remarkGfm], skipHtml: true }, block))));
  assert.equal(pieces.replace(/\n/g, ""), html(text).replace(/\n/g, ""), `blocks: ${JSON.stringify(blocks)}`);
  return blocks;
}

test("paragraphs and headings separated by blank lines become their own blocks", () => {
  const blocks = sameAsWhole("# Title\n\nFirst paragraph\nstill first.\n\nSecond paragraph.\n\n## Next\n\nText.");
  assert.deepEqual(blocks, ["# Title", "First paragraph\nstill first.", "Second paragraph.", "## Next", "Text."]);
});

test("empty text has no blocks, and leading or trailing blank lines are dropped", () => {
  assert.deepEqual(splitMarkdownBlocks(""), []);
  assert.deepEqual(splitMarkdownBlocks("\n\nHello\n\n\n"), ["Hello"]);
});

test("a code fence with blank lines inside stays one block, for ``` and ~~~", () => {
  const backticks = sameAsWhole("Before.\n\n```python\ndef a():\n\n    return 1\n\nprint(a())\n```\n\nAfter.");
  assert.deepEqual(backticks, ["Before.", "```python\ndef a():\n\n    return 1\n\nprint(a())\n```", "After."]);
  const tildes = sameAsWhole("~~~\none\n\n```\nnot a close\n\ntwo\n~~~\n\nAfter.");
  assert.equal(tildes.length, 2);
  assert.match(tildes[0], /two\n~~~$/);
});

test("a fence only closes on the same character with at least its length", () => {
  const blocks = sameAsWhole("````\ncode\n\n```\nstill code\n\n````\n\nAfter.");
  assert.deepEqual(blocks, ["````\ncode\n\n```\nstill code\n\n````", "After."]);
});

test("an unterminated fence keeps everything after it in one block", () => {
  const blocks = splitMarkdownBlocks("Intro.\n\n```js\nconst a = 1;\n\nconst b = 2;\n\nNot prose yet");
  assert.deepEqual(blocks, ["Intro.", "```js\nconst a = 1;\n\nconst b = 2;\n\nNot prose yet"]);
});

test("a loose list with blank lines between items stays one block", () => {
  const bullets = sameAsWhole("- one\n\n- two\n\n- three\n\nAfter.");
  assert.deepEqual(bullets, ["- one\n\n- two\n\n- three", "After."]);
  const ordered = sameAsWhole("1. one\n\n2. two\n\n3. three");
  assert.deepEqual(ordered, ["1. one\n\n2. two\n\n3. three"]);
  sameAsWhole("1) first\n\n2) second");
});

test("nested lists and indented continuation paragraphs stay with their list", () => {
  const blocks = sameAsWhole("- outer\n\n  continued paragraph\n\n  - inner\n\n    inner text\n\n- next\n\nDone.");
  assert.deepEqual(blocks, ["- outer\n\n  continued paragraph\n\n  - inner\n\n    inner text\n\n- next", "Done."]);
});

test("an indented code block after a paragraph is not split from it", () => {
  const blocks = sameAsWhole("Run:\n\n    npm test\n\n    npm run lint\n\nThen check.");
  assert.deepEqual(blocks, ["Run:\n\n    npm test\n\n    npm run lint", "Then check."]);
});

test("a list after a paragraph, and a paragraph between two lists, render the same split", () => {
  sameAsWhole("Steps:\n\n1. a\n2. b\n\nNote.\n\n3. c\n4. d");
  sameAsWhole("Para.\n\n- a\n- b");
});

test("tables stay whole and blockquotes split only where Markdown ends them", () => {
  const blocks = sameAsWhole("| a | b |\n| - | - |\n| 1 | 2 |\n| 3 | 4 |\n\n> quoted\n> more\n>\n> still quoted\n\n> another quote");
  assert.deepEqual(blocks, ["| a | b |\n| - | - |\n| 1 | 2 |\n| 3 | 4 |", "> quoted\n> more\n>\n> still quoted", "> another quote"]);
});

test("reference-style link and footnote definitions keep the text in one block", () => {
  const text = "See [the docs][docs].\n\nMore text.\n\n[docs]: https://example.com";
  assert.deepEqual(splitMarkdownBlocks(text), [text]);
  sameAsWhole(text);
  const footnote = "A claim.[^1]\n\nMore.\n\n[^1]: Source.";
  assert.deepEqual(splitMarkdownBlocks(footnote), [footnote]);
});

test("CRLF line endings split the same way", () => {
  const blocks = sameAsWhole("# Title\r\n\r\nOne.\r\n\r\n```\r\ncode\r\n\r\nmore\r\n```\r\n\r\n- a\r\n\r\n- b");
  assert.equal(blocks.length, 4);
  assert.match(blocks[2], /^```\r\ncode\r\n\r\nmore\r\n```$/);
});

test("inline triple backticks with a backtick in the info string do not open a fence", () => {
  const blocks = sameAsWhole("```inline``` code\n\nNext.");
  assert.deepEqual(blocks, ["```inline``` code", "Next."]);
});

test("a growing answer keeps its earlier blocks unchanged", () => {
  const full = "Intro paragraph.\n\n## Plan\n\n1. first\n\n2. second\n\n```sh\nnpm test\n```\n\nDone.";
  let previous = [];
  for (let end = 1; end <= full.length; end++) {
    const blocks = splitMarkdownBlocks(full.slice(0, end));
    // Every block but the last is final once a later one has started.
    for (let index = 0; index < Math.min(previous.length, blocks.length) - 1; index++) assert.equal(blocks[index], previous[index]);
    previous = blocks;
  }
  sameAsWhole(full);
});

test("a fence indented four spaces is code inside the outer block, never the outer block's end", () => {
  const nested = "```markdown\n1. Step:\n\n    ```bash\n    npm install\n    ```\n\nDone\n```\n\nAfter";
  assert.deepEqual(sameAsWhole(nested), ["```markdown\n1. Step:\n\n    ```bash\n    npm install\n    ```\n\nDone\n```", "After"]);
});

test("an indented ``` outside any fence is indented code and opens nothing", () => {
  sameAsWhole("Intro.\n\n    ```\n    not a fence\n\nParagraph.\n\n```js\nconst a = 1;\n\nconst b = 2;\n```\n\nEnd.");
});
