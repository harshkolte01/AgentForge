import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createSubagentScroll } from '../features/sessions/subagent-scroll.ts';
const at = (scrollTop, scrollHeight = 1000, clientHeight = 400) => ({scrollTop, scrollHeight, clientHeight});

test('opening a running child follows its newest activity; a completed report opens at the top', () => {
  assert.equal(createSubagentScroll(true).layout(at(0)), 600);
  const complete = createSubagentScroll(false);
  assert.equal(complete.layout(at(0)), null);
  assert.equal(complete.jumpVisible(at(0)), true);
});

test('new events and late layout changes keep the live edge in view', () => {
  const scroll = createSubagentScroll(true);
  assert.equal(scroll.layout(at(600, 1250)), 850);
  assert.equal(scroll.layout(at(850, 1250, 250)), 1000);
  assert.equal(scroll.jumpVisible(at(600, 1250)), false);
});

test('scrolling up preserves reading position through new activity and the final report', () => {
  const scroll = createSubagentScroll(true);
  scroll.scrolled(at(200));
  assert.equal(scroll.layout(at(200, 1600)), null);
  assert.equal(scroll.layout(at(200, 3000)), null);
  assert.equal(scroll.jumpVisible(at(200, 3000)), true);
});

test('upward input and opening details pause before new content can pull the reader away', () => {
  const scroll = createSubagentScroll(true);
  scroll.pause();
  assert.equal(scroll.layout(at(600, 1400)), null);
  assert.equal(scroll.jumpVisible(at(600, 1400)), true);
});

test('returning to the bottom or choosing latest resumes following', () => {
  const scroll = createSubagentScroll(false);
  scroll.scrolled(at(595));
  assert.equal(scroll.layout(at(595, 1400)), 1000);
  scroll.pause();
  scroll.resume();
  assert.equal(scroll.layout(at(100, 1600)), 1200);
  assert.equal(scroll.jumpVisible(at(100, 1600)), false);
});

test('short content has no jump control or negative scroll target', () => {
  assert.equal(createSubagentScroll(true).layout(at(0, 120)), 0);
  assert.equal(createSubagentScroll(false).jumpVisible(at(0, 120)), false);
});
