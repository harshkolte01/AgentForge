import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { test } from "node:test";
import { stabilizeMarkdown } from "../lib/markdown-stabilize.ts";

const stabilize = (text) => stabilizeMarkdown(text, { streaming: true });

// [name, streamed prefix, what the renderer should be given]
const cases = [
  // Fences
  ["unclosed backtick fence", "```\ncode", "```\ncode\n```"],
  ["unclosed fence with a language", "```ts\nconst a = 1;", "```ts\nconst a = 1;\n```"],
  ["unclosed fence ending in a newline", "```py\nprint(1)\n", "```py\nprint(1)\n```"],
  ["unclosed tilde fence", "~~~py\nprint(1)\n", "~~~py\nprint(1)\n~~~"],
  ["a longer fence closes with its own length", "````md\n```\ninner", "````md\n```\ninner\n````"],
  ["only the opening line has arrived", "```ts", "```ts\n```"],
  ["only the backticks have arrived", "```", "```\n```"],
  ["a half closing fence is not code", "```js\nx = 1\n``", "```js\nx = 1\n```"],
  ["a closing fence of the wrong kind does not close", "```js\nx\n~~~", "```js\nx\n~~~\n```"],
  ["a closed fence is left alone", "```js\nx = 1\n```", "```js\nx = 1\n```"],
  ["a fence closed by a longer one", "```js\nx\n````", "```js\nx\n````"],
  ["prose after a closed fence is repaired on its own", "```\nx\n```\n\nThen **bold", "```\nx\n```\n\nThen **bold**"],
  ["a fence indented inside a list item", "1. Install\n   ```bash\n   npm i", "1. Install\n   ```bash\n   npm i\n   ```"],
  ["a fence inside a quote", "> ```\n> x", "> ```\n> x\n> ```"],
  ["markup inside an open fence is code, not repaired", "```\n**not bold\n| a |", "```\n**not bold\n| a |\n```"],
  ["blank lines inside a closed fence do not start the last block", "```\na\n\nb\n```\n**bold", "```\na\n\nb\n```\n**bold**"],
  // Tables
  ["a header with no delimiter row yet is held back", "Intro\n\n| a | b |", "Intro\n\n"],
  ["a header and its newline", "| a | b |\n", ""],
  ["a header with a half delimiter row", "| a | b |\n| ---", ""],
  ["a header with a delimiter of the wrong width", "| a | b |\n| --- |", ""],
  ["a header with only a pipe on the next line", "| a | b |\n|", ""],
  ["a complete header and delimiter is a table", "| a | b |\n| --- | --- |", "| a | b |\n| --- | --- |"],
  ["a table with a row in progress", "| a | b |\n|---|---|\n| 1 | 2", "| a | b |\n|---|---|\n| 1 | 2"],
  ["a table row that is only a pipe is held back", "| a | b |\n|---|---|\n| 1 | 2 |\n|", "| a | b |\n|---|---|\n| 1 | 2 |"],
  ["a header without leading pipes keeps its text", "Name | Age", "Name | Age"],
  ["prose before a table is kept", "See below.\n\n| a |", "See below.\n\n"],
  ["emphasis does not span table cells", "| a | b |\n|---|---|\n| **x | y", "| a | b |\n|---|---|\n| **x | y"],
  // Links and images
  ["a link destination still arriving shows its text", "See [the docs](https://exam", "See the docs"],
  ["a link that has only its opening paren", "See [the docs](", "See the docs"],
  ["a link with a nested parenthesis in the url", "[a](http://x.com/f_(g)", "a"],
  ["a closed link is left alone", "See [the docs](https://example.com/a_(b)) now", "See [the docs](https://example.com/a_(b)) now"],
  ["an image still arriving is dropped", "Look ![a diagram](https://exam", "Look "],
  ["an image whose alt text is still arriving is dropped", "Look ![a dia", "Look "],
  ["an image with alt text and no destination yet is dropped", "Look ![a diagram]", "Look "],
  ["an image bang and bracket", "Look ![", "Look "],
  ["a complete image is kept", "Look ![a](https://example.com/i.png) now", "Look ![a](https://example.com/i.png) now"],
  ["link text still arriving is left as typed", "See [the do", "See [the do"],
  ["link text without a destination is left alone", "See [the docs]", "See [the docs]"],
  ["a bracket in code is not a link", "Use `a[0](x` here", "Use `a[0](x` here"],
  // Emphasis, strong, strike, code
  ["unclosed strong", "This is **bold te", "This is **bold te**"],
  ["unclosed emphasis", "This is *slanted", "This is *slanted*"],
  ["unclosed emphasis with underscores", "This is _slanted", "This is _slanted_"],
  ["unclosed strikethrough", "This is ~~gone", "This is ~~gone~~"],
  ["nested openers close in reverse order", "A *b and **c", "A *b and **c***"],
  ["strong then emphasis", "**a *b", "**a *b***"],
  ["trailing space is not kept inside the closer", "**bold ", "**bold**"],
  ["a delimiter with nothing after it is dropped", "Some text **", "Some text "],
  ["a lone star after a space is dropped", "Some text *", "Some text "],
  ["a closed pair is left alone", "**bold** and *it*", "**bold** and *it*"],
  ["a closed pair then a new opener", "**bold** and *it", "**bold** and *it*"],
  ["snake_case is not emphasis", "call snake_case_name now", "call snake_case_name now"],
  ["a star list marker is not emphasis", "* item one\n* item two", "* item one\n* item two"],
  ["emphasis in a list item closes inside that item", "- **a\n- b", "- **a\n- b"],
  ["emphasis in the last list item", "- a\n- **b", "- a\n- **b**"],
  ["emphasis in a heading", "# Title **big", "# Title **big**"],
  ["unclosed inline code", "Run `npm i", "Run `npm i`"],
  ["inline code with nothing in it yet", "Run `", "Run "],
  ["double backtick code", "Run ``a ` b", "Run ``a ` b``"],
  ["emphasis inside inline code is code", "Run `a **b", "Run `a **b`"],
  ["emphasis opened before an unclosed code span", "**bold `code", "**bold `code`**"],
  ["an escaped star is not an opener", "a \\*b", "a \\*b"],
  ["a star between digits is a literal", "3*4=12", "3*4=12"],
  ["a half closing strikethrough", "~~gone~", "~~gone~~"],
  ["a half closing strong", "**bold*", "**bold**"],
  ["a single tilde is a literal", "about ~5 minutes", "about ~5 minutes"],
  // Lists, headings, quotes
  ["a bare bullet is held back", "- one\n-", "- one"],
  ["a bare number is held back", "1. one\n2.", "1. one"],
  ["a bare heading marker is held back", "Intro\n\n##", "Intro\n\n"],
  ["a bare quote marker is held back", "> one\n>", "> one"],
  ["half a fence opener on its own line is held back", "Done.\n\n~~", "Done.\n\n"],
  ["half a backtick fence opener", "Done.\n\n``", "Done.\n\n"],
  ["a strong delimiter alone on a line", "Done.\n\n**", "Done.\n\n"],
  ["a setext underline in progress is held back", "Title\n-", "Title"],
  ["a complete thematic break is kept", "Above\n\n---", "Above\n\n---"],
  ["a bullet with text is kept", "- one\n- two", "- one\n- two"],
  ["a held-back marker followed by a newline", "Title\n-\n", "Title"],
  // Entities and tags
  ["a half entity is dropped", "Fish &am", "Fish "],
  ["a half numeric entity is dropped", "Heart &#x26", "Heart "],
  ["a bare ampersand-hash is dropped", "Heart &#", "Heart "],
  ["a complete entity is kept", "Fish &amp; chips", "Fish &amp; chips"],
  ["an ampersand in prose is kept", "AT&T and Q&A", "AT&T and Q&A"],
  ["a half tag is dropped", "Hello <di", "Hello "],
  ["a half closing tag is dropped", "Hello <b>x</", "Hello <b>x"],
  ["a half tag with an attribute is dropped", "Hello <a href=\"http://x", "Hello "],
  ["a lone angle bracket at the end is dropped", "Hello <", "Hello "],
  ["a complete tag is kept (the renderer skips html)", "Hello <b>x</b>", "Hello <b>x</b>"],
  ["a comparison in prose is kept", "if a < b then", "if a < b then"],
  ["a half comment is dropped", "Hello <!-- note", "Hello "],
  // Unicode
  ["a lone high surrogate at the end is dropped", "smile \ud83d", "smile "],
  ["a complete emoji is kept", "smile 😀", "smile 😀"],
  ["a lone low surrogate is dropped", "a\ude00b", "ab"],
  ["CJK strong", "这是**加粗的文", "这是**加粗的文**"],
  ["CJK strong closed", "这是**加粗**的文字", "这是**加粗**的文字"],
  ["Japanese text with an emoji and a list", "- 日本語 😀\n- テキスト **太", "- 日本語 😀\n- テキスト **太**"],
  ["RTL text with strong", "مرحبا **عالم", "مرحبا **عالم**"],
  ["RTL text complete", "مرحبا **عالم** ✓", "مرحبا **عالم** ✓"],
  ["combining marks and ZWJ sequences", "é 👨‍👩‍👧 *ok", "é 👨‍👩‍👧 *ok*"],
  // Plain text
  ["plain text is untouched", "Just words.", "Just words."],
  ["an empty string", "", ""],
  ["only whitespace", "  \n ", "  \n "],
];

for (const [name, input, expected] of cases) {
  test(`stabilize: ${name}`, () => {
    assert.equal(stabilize(input), expected);
    assert.equal(stabilize(stabilize(input)), stabilize(input), "stabilizing again must change nothing");
  });
}

test("a finished answer is returned untouched whatever it ends with", () => {
  for (const [, input] of cases) assert.equal(stabilizeMarkdown(input, { streaming: false }), input);
});

// A document that exercises every construct, with no literal syntax characters in its prose.
const documents = [
  [
    "# Streaming demo",
    "",
    "Here is **bold text**, *italic*, ~~strike~~ and `inline code` with a [link](https://example.com/a_(b)) and an image ![alt text](https://example.com/i.png).",
    "",
    "- first item with **strong** words",
    "- second item with `code`",
    "  - nested item *emphasis*",
    "",
    "1. one",
    "2. two",
    "",
    "> quote with **bold**",
    "",
    "| Name | Age | City |",
    "| --- | :---: | ---: |",
    "| Ada | 36 | London |",
    "| Linus | 54 | Portland |",
    "",
    "```python",
    "def f(x):",
    "    return x",
    "```",
    "",
    "~~~js",
    "console.log(1);",
    "~~~",
    "",
    "Unicode: 日本語のテキスト **太字** and emoji 😀👍 and مرحبا **عالم** done.",
    "",
    "Fish &amp; chips and <span>inline html</span> end.",
  ].join("\n"),
  "Short answer with **one** bold word.",
  ["1. Install", "   ```bash", "   npm install", "   ```", "2. Run `npm test` and read the **output**."].join("\n"),
];

test("complete, well-formed text comes back unchanged", () => {
  for (const document of documents) assert.equal(stabilize(document), document);
});

test("every prefix stabilizes without throwing, and stabilizing is idempotent", () => {
  for (const document of documents) {
    for (let end = 0; end <= document.length; end++) {
      const prefix = document.slice(0, end);
      const once = stabilize(prefix);
      assert.equal(typeof once, "string");
      assert.equal(stabilize(once), once, `not idempotent at ${end}: ${JSON.stringify(prefix.slice(-30))}`);
    }
  }
});

test("a growing prefix never rewrites what the reader already has, except at its tail", () => {
  // The stabilized text of a longer prefix keeps the completed blocks of a shorter one: only the last block moves.
  for (const document of documents) {
    let previousHead = "";
    for (let end = 0; end <= document.length; end++) {
      const stabilized = stabilize(document.slice(0, end));
      const lastBlank = stabilized.lastIndexOf("\n\n");
      const head = lastBlank === -1 ? "" : stabilized.slice(0, lastBlank);
      if (previousHead && !document.slice(0, end).includes("```") && !document.slice(0, end).includes("~~~")) assert.ok(head.startsWith(previousHead) || previousHead.startsWith(head), `the head changed at ${end}`);
      previousHead = head.length >= previousHead.length ? head : previousHead;
    }
  }
});

// Rendering: what the stabilized text looks like once parsed ---------------------------------------------------------

const render = (text) => renderToStaticMarkup(React.createElement(Markdown, { remarkPlugins: [remarkGfm], skipHtml: true }, text));
const visible = (html) => html.replace(/<[^>]*>/g, "").replaceAll("&amp;", "&").replaceAll("&lt;", "<").replaceAll("&gt;", ">").replaceAll("&quot;", "\"").replaceAll("&#x27;", "'");

test("no prefix of any demo document shows raw syntax once rendered", () => {
  for (const document of documents) for (let end = 1; end <= document.length; end++) {
    const text = visible(render(stabilize(document.slice(0, end))));
    const where = `at ${end}: ${JSON.stringify(document.slice(Math.max(0, end - 25), end))}`;
    assert.ok(!/\*\*|~~|```|~~~|\]\(|!\[|\||�/.test(text), `raw syntax shows ${where} -> ${JSON.stringify(text.slice(-60))}`);
    assert.ok(!/&[a-z]{2}|&#/i.test(text), `a half entity shows ${where}`);
    assert.ok(!/<[a-z/]/i.test(text), `a half tag shows ${where}`);
    assert.ok(!/[\ud800-\udfff]/.test(text.replace(/[\ud800-\udbff][\udc00-\udfff]/g, "")), `a lone surrogate shows ${where}`);
  }
});

test("rendered, an open fence is a code block from its first line and an open strong is bold", () => {
  assert.match(render(stabilize("Intro\n\n```ts\nconst a = 1;")), /<pre><code class="language-ts">const a = 1;\n<\/code><\/pre>/);
  assert.match(render(stabilize("This is **bold te")), /<strong>bold te<\/strong>/);
  assert.match(render(stabilize("| a | b |\n| --- | --- |\n| 1 | 2")), /<table>[\s\S]*<td>1<\/td>[\s\S]*<td>2<\/td>/);
  assert.doesNotMatch(render(stabilize("| a | b |")), /\|/);
  assert.match(render(stabilize("See [docs](https://exa")), /^<p>See docs<\/p>$/);
});

test("a large answer stabilizes quickly", () => {
  const big = documents[0].repeat(60) + "\n\n**tail";
  const started = performance.now();
  for (let i = 0; i < 20; i++) stabilize(big);
  const each = (performance.now() - started) / 20;
  assert.ok(each < 30, `${big.length} characters took ${each.toFixed(1)}ms per call`);
  assert.ok(stabilize(big).endsWith("**tail**"));
});
