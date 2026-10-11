import assert from "node:assert/strict";
import { test } from "node:test";
import { splitAssistantContent } from "../features/sessions/assistant-content.ts";

const answer = (content, streaming = false) => splitAssistantContent(content, streaming).answer;

test("ordinary text passes through unchanged", () => {
  assert.deepEqual(splitAssistantContent("Hello **world**"), { answer: "Hello **world**", hadLeadingThoughts: false });
  assert.equal(answer(""), "");
});

test("a leading think or thinking block is metadata: stripped, never returned", () => {
  assert.deepEqual(splitAssistantContent("<think>let me see</think>\n\nThe answer."), { answer: "The answer.", hadLeadingThoughts: true });
  assert.equal(answer("<thinking>a</thinking>The answer."), "The answer.");
  assert.equal(answer("  \n<think>a</think><think>b</think>  Done."), "Done.", "several leading blocks, and leading whitespace");
});

test("the same tags later in the answer are the answer's own words and stay", () => {
  const example = "Use the tag like this: <think>x</think> in your prompt.";
  assert.equal(answer(example), example);
  assert.equal(answer("<think>hidden</think>Wrap it as <think>x</think>."), "Wrap it as <think>x</think>.");
  assert.equal(answer("Before <think>kept</think>"), "Before <think>kept</think>");
  assert.equal(answer("```html\n<think>kept</think>\n```"), "```html\n<think>kept</think>\n```");
});

test("while streaming, a block that has not closed shows nothing, and a half closing tag is not an answer", () => {
  assert.equal(answer("<think>still reasoning", true), "");
  assert.equal(answer("<think>still reasoning</thi", true), "");
  assert.equal(answer("<think>done</think>", true), "");
  assert.equal(answer("<think>done</think>Visible so far", true), "Visible so far");
});

test("while streaming, a split opening tag is held back until it is clear what it is", () => {
  for (const prefix of ["<", "<t", "<thin", "<think", "<think>", "<thinking", "<thinking>"]) assert.equal(answer(prefix, true), "", prefix);
  assert.equal(answer("<table>", true), "<table>", "a tag that stops matching is shown");
  assert.equal(answer("<b>bold</b>", true), "<b>bold</b>");
});

test("a complete answer that merely starts with an unclosed tag is metadata too (as before)", () => {
  assert.equal(answer("<think>never closed"), "");
});
