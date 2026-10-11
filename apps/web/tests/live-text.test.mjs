import assert from "node:assert/strict";
import { test } from "node:test";
import {
  applySnapshot, codePointLength, createLiveState, emptyLive, liveHandoff, parseLiveDelta, reduceLive, skipCodePoints, visibleLive,
} from "../lib/activity/live-text.ts";

const frame = (extra) => ({ task_id: "t", epoch: 1, revision: 0, kind: "progress", offset: 0, text: "", reset: false, ...extra });
const snapshot = (extra) => ({ task_id: "t", epoch: 0, revision: 0, kind: "progress", text: "", event_sequence: 0, terminal: false, ...extra });
const apply = (state, ...frames) => frames.reduce((current, next) => reduceLive(current, next).state, state);

test("growth appends at the end of the text", () => {
  let state = reduceLive(createLiveState(), frame({ text: "Hello", reset: true })).state;
  assert.deepEqual([state.text, state.epoch, state.length], ["Hello", 1, 5]);
  const update = reduceLive(state, frame({ offset: 5, text: ", world" }));
  assert.deepEqual([update.state.text, update.status, update.needsSnapshot], ["Hello, world", "applied", false]);
});

test("an older epoch never replaces newer text", () => {
  const state = apply(createLiveState(), frame({ epoch: 3, text: "newest", reset: true }));
  const update = reduceLive(state, frame({ epoch: 2, text: "older text", reset: true }));
  assert.equal(update.status, "stale");
  assert.equal(update.state, state);
  assert.equal(reduceLive(state, frame({ epoch: 1, offset: 6, text: "x" })).state, state);
});

test("within one epoch an older revision is stale", () => {
  const state = apply(createLiveState(), frame({ epoch: 2, revision: 4, text: "kept", reset: true }));
  for (const revision of [0, 3]) {
    const update = reduceLive(state, frame({ epoch: 2, revision, text: "rewound", reset: true }));
    assert.deepEqual([update.status, update.state], ["stale", state]);
  }
});

test("a duplicate frame is ignored, and the state object does not change", () => {
  const state = apply(createLiveState(), frame({ text: "Hello", reset: true }), frame({ offset: 5, text: " there" }));
  for (const repeated of [frame({ offset: 5, text: " there" }), frame({ offset: 0, text: "Hello", reset: true }), frame({ offset: 2, text: "llo" })]) {
    const update = reduceLive(state, repeated);
    assert.deepEqual([update.status, update.needsSnapshot], ["duplicate", false]);
    assert.equal(update.state, state);
  }
  assert.equal(reduceLive(state, frame({ offset: 11, text: "" })).state, state, "an empty frame at the end adds nothing");
});

test("a frame that overlaps what is held adds only the new part", () => {
  const state = apply(createLiveState(), frame({ text: "Hello", reset: true }));
  const update = reduceLive(state, frame({ offset: 3, text: "lo, world" }));
  assert.deepEqual([update.state.text, update.status], ["Hello, world", "applied"]);
});

test("a reset replaces the whole text, in a newer epoch or revision", () => {
  const state = apply(createLiveState(), frame({ text: "first attempt", reset: true }), frame({ offset: 13, text: " goes on" }));
  const restarted = reduceLive(state, frame({ revision: 1, text: "second", reset: true }));
  assert.deepEqual([restarted.state.text, restarted.state.revision, restarted.status], ["second", 1, "reset"]);
  const next = reduceLive(restarted.state, frame({ epoch: 2, revision: 0, kind: "answer", text: "the answer", reset: true }));
  assert.deepEqual([next.state.text, next.state.epoch, next.state.kind], ["the answer", 2, "answer"]);
});

test("an empty reset is the explicit 'this text was cleared' signal", () => {
  const state = apply(createLiveState(), frame({ text: "to be discarded", reset: true }));
  const cleared = reduceLive(state, frame({ revision: 1, text: "", reset: true }));
  assert.deepEqual([cleared.state.text, cleared.state.length, cleared.status], ["", 0, "reset"]);
  // Growth continues from the cleared text.
  assert.equal(reduceLive(cleared.state, frame({ revision: 1, offset: 0, text: "fresh" })).state.text, "fresh");
  // A late frame of the discarded text cannot bring it back.
  assert.equal(reduceLive(cleared.state, frame({ revision: 0, offset: 15, text: "!" })).status, "stale");
  assert.equal(reduceLive(cleared.state, frame({ revision: 0, text: "to be discarded", reset: true })).status, "stale");
});

test("a frame that starts beyond the text means a frame was missed: fetch the snapshot", () => {
  const state = apply(createLiveState(), frame({ text: "abc", reset: true }));
  const gap = reduceLive(state, frame({ offset: 10, text: "xyz" }));
  assert.deepEqual([gap.status, gap.needsSnapshot, gap.state], ["mismatch", true, state]);
  // A newer segment that is not a reset and not at offset 0 cannot stand alone either.
  const newer = reduceLive(state, frame({ epoch: 2, offset: 4, text: "tail" }));
  assert.deepEqual([newer.status, newer.needsSnapshot, newer.state], ["mismatch", true, state]);
  const newerRevision = reduceLive(state, frame({ revision: 1, offset: 4, text: "tail" }));
  assert.equal(newerRevision.needsSnapshot, true);
  // From nothing at all, a mid-text frame is a mismatch too.
  assert.equal(reduceLive(emptyLive, frame({ offset: 7, text: "mid" })).needsSnapshot, true);
});

test("a newer version starting at offset 0 is accepted even when it is not flagged as a reset", () => {
  const state = apply(createLiveState(), frame({ text: "old", reset: true }));
  assert.equal(reduceLive(state, frame({ epoch: 2, offset: 0, text: "new" })).state.text, "new");
});

test("offsets count Unicode code points, as the server does", () => {
  let state = apply(createLiveState(), frame({ text: "a😀b", reset: true }));
  assert.deepEqual([state.length, state.text.length], [3, 4]);
  state = reduceLive(state, frame({ offset: 3, text: "👍!" })).state; // the server says offset 3: after a, 😀, b
  assert.equal(state.text, "a😀b👍!");
  assert.equal(state.length, 5);
  assert.equal(reduceLive(state, frame({ offset: 3, text: "👍!" })).status, "duplicate");
  const overlap = reduceLive(state, frame({ offset: 2, text: "b👍!?" }));
  assert.equal(overlap.state.text, "a😀b👍!?");
  assert.equal(codePointLength("日本語😀"), 4);
  assert.equal(skipCodePoints("a😀bc", 2), "bc");
  assert.equal(skipCodePoints("abc", 0), "abc");
  assert.equal(skipCodePoints("abc", 9), "");
});

test("a fuzzed delivery that drops, repeats and reorders frames converges once snapshots repair the gaps", () => {
  let seed = 12345;
  const random = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  const alphabet = ["a", "b", "😀", "日", "é", " ", "ع"];
  // The server: grows a text, sometimes clears it (revision) and sometimes opens a new epoch.
  const truth = { epoch: 1, revision: 0, kind: "progress", text: "" };
  const wire = [];
  const push = (reset, offset, text) => wire.push({ ...frame({ epoch: truth.epoch, revision: truth.revision, kind: truth.kind, offset, text, reset }) });
  let length = 0;
  for (let step = 0; step < 400; step++) {
    const roll = random();
    if (roll < 0.05) { truth.revision++; truth.text = ""; length = 0; push(true, 0, ""); }
    else if (roll < 0.08) { truth.epoch++; truth.revision = 0; truth.text = ""; length = 0; push(true, 0, ""); }
    else {
      const piece = Array.from({ length: 1 + Math.floor(random() * 4) }, () => alphabet[Math.floor(random() * alphabet.length)]).join("");
      push(false, length, piece);
      truth.text += piece;
      length += codePointLength(piece);
    }
  }
  // Truth after the first n frames, for snapshots.
  const truthAfter = (n) => {
    let current = { epoch: 1, revision: 0, kind: "progress", text: "" };
    for (const item of wire.slice(0, n)) {
      if (item.reset) current = { epoch: item.epoch, revision: item.revision, kind: item.kind, text: item.text };
      else current = { ...current, text: current.text + item.text };
    }
    return current;
  };
  let state = createLiveState();
  const delivered = [];
  wire.forEach((item, index) => {
    const roll = random();
    if (roll < 0.1) return;                       // lost
    delivered.push([index, item]);
    if (roll > 0.85) delivered.push([index, item]); // repeated
  });
  // Light reordering: swap some neighbours.
  for (let i = 0; i + 1 < delivered.length; i++) if (random() < 0.1) [delivered[i], delivered[i + 1]] = [delivered[i + 1], delivered[i]];
  // At every step the text shown is a prefix of the text its (epoch, revision) ends up with, and versions never go backwards.
  const finalText = new Map();
  for (let n = 1; n <= wire.length; n++) { const at = truthAfter(n); finalText.set(`${at.epoch}:${at.revision}`, at.text); }
  let highest = 0;
  let version = [0, 0];
  for (const [index, item] of delivered) {
    highest = Math.max(highest, index + 1);
    const update = reduceLive(state, item);
    state = update.state;
    if (update.needsSnapshot) state = applySnapshot(state, snapshot({ ...truthAfter(highest) }));
    assert.ok(state.epoch > version[0] || (state.epoch === version[0] && state.revision >= version[1]), "version went backwards");
    version = [state.epoch, state.revision];
    if (state.epoch > 0) assert.ok(finalText.get(`${state.epoch}:${state.revision}`).startsWith(state.text), `text diverged at frame ${index}`);
  }
  state = applySnapshot(state, snapshot({ ...truthAfter(wire.length) })); // a final poll
  assert.equal(state.text, truth.text);
  assert.deepEqual([state.epoch, state.revision], [truth.epoch, truth.revision]);
});

test("a snapshot replaces the state when it is newer or longer, and never rewinds it", () => {
  const state = apply(createLiveState(), frame({ epoch: 2, revision: 1, text: "held text", reset: true }));
  assert.equal(applySnapshot(state, snapshot({ epoch: 3, text: "newer segment", kind: "answer" })).text, "newer segment");
  assert.equal(applySnapshot(state, snapshot({ epoch: 2, revision: 2, text: "" })).text, "");
  assert.equal(applySnapshot(state, snapshot({ epoch: 2, revision: 1, text: "held text and more" })).text, "held text and more");
  assert.equal(applySnapshot(state, snapshot({ epoch: 2, revision: 1, text: "held" })), state, "read a moment earlier than the stream: keep what we have");
  assert.equal(applySnapshot(state, snapshot({ epoch: 2, revision: 1, text: "held text" })), state);
  assert.equal(applySnapshot(state, snapshot({ epoch: 1, revision: 9, text: "stale" })), state);
  assert.equal(applySnapshot(emptyLive, snapshot()), emptyLive, "no live row: nothing to show");
  const answer = applySnapshot(state, snapshot({ epoch: 2, revision: 1, text: "held text", kind: "answer" }));
  assert.equal(answer.kind, "answer");
});

test("live text is hidden only when its durable counterpart exists, and unchanged until then", () => {
  const live = apply(createLiveState(), frame({ epoch: 3, text: "I will search now", reset: true }));
  // No note saved yet for epoch 3: the live copy is the only copy.
  assert.equal(visibleLive(live, 0, null), "I will search now");
  assert.equal(visibleLive(live, 2, null), "I will search now");
  assert.deepEqual(liveHandoff(live, 2, ""), { text: "I will search now", handedOffTo: null });
  // The note of epoch 3 (or a later one) is in the timeline: the live copy steps aside.
  assert.deepEqual(liveHandoff(live, 3, null), { text: "", handedOffTo: "note" });
  assert.equal(visibleLive(live, 5, null), "");
  // The next epoch's live text is still new.
  const next = apply(live, frame({ epoch: 4, text: "Next", reset: true }));
  assert.equal(visibleLive(next, 3, null), "Next");
});

test("the saved answer takes over from the live answer", () => {
  const live = apply(createLiveState(), frame({ epoch: 6, kind: "answer", text: "The answer is 42.", reset: true }));
  assert.equal(visibleLive(live, 2, undefined), "The answer is 42.");
  assert.equal(visibleLive(live, 2, "   "), "The answer is 42.", "a blank saved answer is not the answer yet");
  assert.deepEqual(liveHandoff(live, 2, "The answer is 42."), { text: "", handedOffTo: "answer" });
  assert.equal(visibleLive(live, 2, "The answer is 42, with sources."), "", "the durable text is authoritative even when it differs");
  assert.equal(visibleLive(emptyLive, 0, null), "");
});

test("parseLiveDelta accepts the frame and fills what older servers omit", () => {
  assert.deepEqual(parseLiveDelta({ task_id: "t", epoch: 2, offset: 4, text: "ab" }), { task_id: "t", epoch: 2, revision: 0, kind: "progress", offset: 4, text: "ab", reset: false });
  assert.deepEqual(parseLiveDelta({ task_id: "t", epoch: 2, revision: 3, kind: "answer", offset: 0, text: "", reset: true }), { task_id: "t", epoch: 2, revision: 3, kind: "answer", offset: 0, text: "", reset: true });
  assert.equal(parseLiveDelta({ task_id: "t", epoch: 2, kind: "bogus", offset: 0, text: "" }).kind, "progress");
  for (const bad of [null, "x", 5, {}, { task_id: "t", epoch: 1, offset: 0 }, { task_id: "t", epoch: -1, offset: 0, text: "" }, { task_id: "t", epoch: 1, offset: 1.5, text: "" }, { epoch: 1, offset: 0, text: "" }]) {
    assert.equal(parseLiveDelta(bad), null, JSON.stringify(bad));
  }
});
