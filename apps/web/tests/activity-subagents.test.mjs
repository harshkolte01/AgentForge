import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createActivityState, reduceActivity } from '../lib/activity/reducer.ts';
import { isDetailable } from '../lib/activity/presentation.ts';

const event = (sequence, event_type, payload) => ({task_id: 'task', sequence, event_type, payload, created_at: '2026-10-11T00:00:00Z'});
const events = [
  event(1, 'tool.started', {tool: 'delegate_task', tool_call_id: 'parent-call', agent_run_id: 'parent', detail: 'Review authentication'}),
  event(2, 'tool.started', {tool: 'web_search', tool_call_id: 'child-call', agent_run_id: 'child', parent_run_id: 'parent'}),
  event(3, 'assistant.message', {text: 'Child progress', agent_run_id: 'child', parent_run_id: 'parent'}),
  event(4, 'tool.finished', {tool: 'web_search', tool_call_id: 'child-call', agent_run_id: 'child', parent_run_id: 'parent', outcome: 'completed'}),
  event(5, 'tool.finished', {tool: 'delegate_task', tool_call_id: 'parent-call', agent_run_id: 'parent', outcome: 'completed'}),
];

test('child events remain contiguous without leaking into the main timeline or counts', () => {
  const state = reduceActivity(createActivityState('task'), {type: 'events', events});
  assert.equal(state.contiguous, 5);
  assert.equal(state.actionCount, 1);
  assert.equal(state.noteCount, 0);
  assert.equal(state.view.length, 1);
  assert.equal(state.view[0].outcome, 'completed');
  assert.equal(state.view[0].detail, 'Review authentication');
  assert.equal(state.view[0].detailable, true);
});

test('assignments are inspectable while running and after cancellation', () => {
  assert.equal(isDetailable('subagent', 'id', 'running'), true);
  assert.equal(isDetailable('subagent', 'id', 'cancelled'), true);
  assert.equal(isDetailable('subagent', null, 'running'), false);
  const running = reduceActivity(createActivityState('task'), {type: 'events', events: events.slice(0, 3)});
  assert.equal(running.view[0].detailable, true);
  const stopped = reduceActivity(running, {type: 'terminal', status: 'CANCELLED'});
  assert.notEqual(stopped.view[0].outcome, 'completed');
  assert.notEqual(stopped.view[0].outcome, 'running');
});

test('late and repeated child events cannot duplicate the parent assignment', () => {
  let state = createActivityState('task');
  for (const row of [...events].reverse()) state = reduceActivity(state, {type: 'events', events: [row, row]});
  assert.equal(state.actionCount, 1);
  assert.equal(state.view.length, 1);
  assert.equal(state.contiguous, 5);
});

// A team of subagents ---------------------------------------------------------------------------------------------------

const at = (sequence, second) => new Date(Date.UTC(2026, 9, 11, 0, 0, second)).toISOString();
const row = (sequence, event_type, payload, second = sequence) => ({task_id: 'task', sequence, event_type, payload, created_at: at(sequence, second)});
const delegate = (sequence, n, title) => row(sequence, 'tool.started', {tool: 'delegate_task', tool_call_id: `d${n}`, agent_run_id: 'parent', child_run_id: `child-${n}`, detail: title});
const childStart = (sequence, n, id, tool, detail) => row(sequence, 'tool.started', {tool, tool_call_id: id, agent_run_id: `child-${n}`, parent_run_id: 'parent', ...(detail ? {detail} : {})});
const childFinish = (sequence, n, id, tool) => row(sequence, 'tool.finished', {tool, tool_call_id: id, agent_run_id: `child-${n}`, parent_run_id: 'parent', outcome: 'completed'});
const childNote = (sequence, n, text) => row(sequence, 'assistant.message', {text, agent_run_id: `child-${n}`, parent_run_id: 'parent'});
const team = [
  delegate(1, 1, 'Auth flow'), delegate(2, 2, 'Billing'), delegate(3, 3, 'Search index'),
  childStart(4, 1, 'a1', 'web_search', 'session cookies'),
  childStart(5, 2, 'b1', 'read_repo_file'),
  childNote(6, 2, 'Reading the invoice model.\nThen the tax rules.'),
  childFinish(7, 1, 'a1', 'web_search'),
  childStart(8, 1, 'a2', 'fetch_webpage'),
];
const entry = (state, title) => state.view.find((item) => item.detail === title);

test('each subagent card follows its own child, even when the children work at the same time', () => {
  const state = reduceActivity(createActivityState('task'), {type: 'events', events: team});
  assert.equal(state.view.length, 3);
  assert.equal(state.actionCount, 3, 'the children\'s calls are not counted as the main run\'s own actions');
  const [auth, billing, search] = ['Auth flow', 'Billing', 'Search index'].map((title) => entry(state, title));
  assert.equal(auth.childRunId, 'child-1');
  assert.deepEqual(auth.child, {steps: 2, active: [{id: 'a2', label: 'Reading a web page'}], note: null});
  assert.equal(billing.child.steps, 1);
  assert.deepEqual(billing.child.active.map((item) => item.label), ['Reading a file']);
  assert.equal(billing.child.note, 'Reading the invoice model.\nThen the tax rules.');
  assert.deepEqual(search.child, {steps: 0, active: [], note: null});
  assert.equal(state.contiguous, 8);
});

test('a running card is the very object the reducer tracks as running, so its finish still finds it', () => {
  let state = reduceActivity(createActivityState('task'), {type: 'events', events: team});
  state = reduceActivity(state, {type: 'events', events: [childFinish(9, 1, 'a2', 'fetch_webpage'),
    row(10, 'tool.finished', {tool: 'delegate_task', tool_call_id: 'd1', agent_run_id: 'parent', outcome: 'completed', duration_ms: 12000})]});
  const auth = entry(state, 'Auth flow');
  assert.equal(auth.outcome, 'completed');
  assert.equal(auth.durationMs, 12000);
  assert.deepEqual(auth.child.active, []);
  assert.equal(auth.child.steps, 2, 'what it did stays on the finished card');
  assert.equal(entry(state, 'Billing').outcome, 'running');
  assert.equal(state.running.length, 2);
});

test('the same cards come out whatever order the events arrive in', () => {
  const forward = reduceActivity(createActivityState('task'), {type: 'events', events: team});
  let shuffled = createActivityState('task');
  for (const index of [7, 2, 5, 0, 6, 3, 1, 4]) shuffled = reduceActivity(shuffled, {type: 'events', events: [team[index]]});
  assert.deepEqual(shuffled.view.map((item) => [item.detail, item.child]), forward.view.map((item) => [item.detail, item.child]));
});

test('a child event whose card is not known, or from a run that did not name its child, changes nothing', () => {
  const legacy = [row(1, 'tool.started', {tool: 'delegate_task', tool_call_id: 'd1', agent_run_id: 'parent', detail: 'Old run'}), childStart(2, 1, 'a1', 'web_search')];
  const state = reduceActivity(createActivityState('task'), {type: 'events', events: legacy});
  assert.equal(state.view.length, 1);
  assert.equal(state.view[0].child, undefined);
  assert.equal(state.view[0].childRunId, undefined);
  const stray = reduceActivity(createActivityState('task'), {type: 'events', events: [childStart(1, 9, 'x', 'web_search')]});
  assert.equal(stray.view.length, 0);
  assert.equal(stray.actionCount, 0);
});

test('a long note is cut for the card, and hidden tools and empty notes are not shown', () => {
  const long = 'x'.repeat(900);
  const state = reduceActivity(createActivityState('task'), {type: 'events', events: [delegate(1, 1, 'Auth flow'),
    childNote(2, 1, long), childNote(3, 1, '   '), childStart(4, 1, 'h1', 'read_task_input')]});
  const card = state.view[0];
  assert.equal(card.child.note.length, 401);
  assert.ok(card.child.note.endsWith('…'));
  assert.equal(card.child.steps, 0);
});

test('a team that is cut off when the run ends is shown as interrupted, with what each had done', () => {
  const state = reduceActivity(reduceActivity(createActivityState('task'), {type: 'events', events: team}), {type: 'terminal', status: 'CANCELLED', sequence: 8});
  assert.deepEqual(state.view.map((item) => item.outcome), ['unknown', 'unknown', 'unknown']);
  assert.equal(entry(state, 'Billing').child.steps, 1);
});

test('the start event is the card\'s clock', () => {
  const state = reduceActivity(createActivityState('task'), {type: 'events', events: [row(1, 'tool.started', {tool: 'delegate_task', tool_call_id: 'd1', agent_run_id: 'parent', child_run_id: 'c', detail: 'Timed'}, 5)]});
  assert.equal(state.view[0].startedAt, Date.UTC(2026, 9, 11, 0, 0, 5));
  const other = reduceActivity(createActivityState('task'), {type: 'events', events: [row(1, 'tool.started', {tool: 'web_search', tool_call_id: 'w1', agent_run_id: 'parent'})]});
  assert.equal('startedAt' in other.view[0], false, 'only delegations carry a clock');
});
