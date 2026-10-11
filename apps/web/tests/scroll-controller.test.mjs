import assert from "node:assert/strict";
import { test } from "node:test";
import { createScrollController, isScrollbarPointerDown, keyScrollDirection, liveEdgeTop, reservedRoom } from "../lib/scroll-controller.ts";
import { column, scrollHarness } from "./helpers/scroll-harness.mjs";

/** A long conversation the reader has already landed at the bottom of. */
function landed(extra = {}) {
  const h = scrollHarness({ blocks: column(10), ...extra });
  h.controller.landOnLatest({ turn: false });
  h.tick();
  h.settle();
  return h;
}
const pausedAt = (h, top) => { h.userScroll(top); h.controller.input("wheel", -100); };

test("pure helpers: room, live edge, keys and the scrollbar gutter", () => {
  assert.equal(reservedRoom(800, 120, 40), 640);
  assert.equal(reservedRoom(100, 120, 40), 0, "never negative");
  assert.equal(liveEdgeTop({ top: 500, below: 200 }, 16), 716);
  const plain = { editable: false, activates: false };
  assert.equal(keyScrollDirection("PageUp", false, plain), "up");
  assert.equal(keyScrollDirection("Home", false, plain), "up");
  assert.equal(keyScrollDirection("ArrowUp", false, plain), "up");
  assert.equal(keyScrollDirection("PageDown", false, plain), "down");
  assert.equal(keyScrollDirection("End", false, plain), "down");
  assert.equal(keyScrollDirection("ArrowDown", false, plain), "down");
  assert.equal(keyScrollDirection(" ", false, plain), "down");
  assert.equal(keyScrollDirection(" ", true, plain), "up", "Shift+Space scrolls up");
  assert.equal(keyScrollDirection("a", false, plain), null);
  assert.equal(keyScrollDirection("Enter", false, plain), null);
  assert.equal(keyScrollDirection("ArrowUp", false, { editable: true, activates: false }), null, "typing in the composer is not scrolling");
  assert.equal(keyScrollDirection(" ", false, { editable: false, activates: true }), null, "Space on a button presses it");
  const press = { targetIsScroller: true, pointerType: "mouse", clientX: 990, right: 1000, scrollbarWidth: 15 };
  assert.equal(isScrollbarPointerDown(press), true);
  assert.equal(isScrollbarPointerDown({ ...press, clientX: 900 }), false, "inside the content area");
  assert.equal(isScrollbarPointerDown({ ...press, targetIsScroller: false }), false, "a child was pressed");
  assert.equal(isScrollbarPointerDown({ ...press, pointerType: "touch" }), false);
  assert.equal(isScrollbarPointerDown({ ...press, scrollbarWidth: 0, clientX: 992 }), true, "overlay scrollbars can still be grabbed at the edge");
  assert.equal(isScrollbarPointerDown({ ...press, scrollbarWidth: 0, clientX: 900 }), false);
});

test("landing on the latest content follows the live edge and reserves room from the measurements", () => {
  const h = landed();
  assert.deepEqual(h.state.rooms, [460], "600 - 100 composer - 40 clearance");
  assert.equal(h.snapshot().following, true);
  assert.equal(h.state.top, h.maxTop());
  assert.equal(h.snapshot().jumpVisible, false);
});

test("following: growth at the live edge scrolls to keep the end in view; content that fits does not scroll", () => {
  const h = landed();
  h.grow("b9", 300);
  h.layout();
  assert.equal(h.host.metrics().below <= 0, true);
  assert.equal(h.state.top, h.maxTop());
  const short = scrollHarness({ blocks: [{ id: "a", h: 100 }] });
  short.controller.landOnLatest({ turn: false });
  short.tick();
  assert.deepEqual(short.state.scrolls, [], "nothing hidden below, nothing to do");
});

test("following ignores layout churn that needs no scrolling and writes the room only when it changes", () => {
  const h = landed();
  const scrolls = h.state.scrolls.length;
  h.layout(); h.layout();
  assert.equal(h.state.scrolls.length, scrolls);
  assert.equal(h.state.rooms.length, 1);
  h.state.viewport = 500; // the keyboard opened
  h.layout();
  assert.deepEqual(h.state.rooms, [460, 360]);
});

test("every kind of reader input pauses following and records why", () => {
  const cases = [
    ["wheel up", (h) => h.controller.input("wheel", -80), "wheel"],
    ["wheel down", (h) => { h.userScroll(500); h.controller.input("wheel", 80); }, "wheel"],
    ["touch drag", (h) => h.controller.input("touch", -40), "touch"],
    ["keyboard", (h) => h.controller.input("keyboard", -1), "keyboard"],
    ["scrollbar drag", (h) => h.controller.scrollbarDown(), "scrollbar"],
    ["text selection", (h) => h.controller.selection(true), "selection"],
    ["disclosure toggle", (h) => h.controller.toggle({ id: "b9", offset: 100 }), "toggle"],
    ["earlier messages", (h) => h.controller.holdPlace(), "history"],
  ];
  for (const [name, act, reason] of cases) {
    const h = landed();
    act(h);
    assert.equal(h.snapshot().following, false, name);
    assert.equal(h.snapshot().pausedBy, reason, name);
  }
});

test("while paused, growth does not move the view and the jump button appears once real content is below", () => {
  const h = landed();
  h.userScroll(900);
  h.controller.input("wheel", -100);
  const top = h.state.top;
  const before = h.state.scrolls.length;
  h.grow("b9", 500);
  h.layout();
  assert.equal(h.state.top, top, "the reader is not dragged to the bottom");
  assert.equal(h.state.scrolls.length, before, "no programmatic scroll at all, anchor is unmoved");
  assert.equal(h.snapshot().jumpVisible, true);
});

test("jump counts real content below the view, never the room reserved under the latest turn", () => {
  const h = scrollHarness({ blocks: column(3), turn: "b2" });
  h.state.room = 460;
  h.controller.landOnLatest({ turn: false });
  h.tick();
  h.settle();
  h.userScroll(0);
  h.controller.input("wheel", 100);
  assert.equal(h.snapshot().following, false);
  assert.equal(h.host.metrics().height - h.state.viewport > 400, true, "plenty of blank room to scroll into");
  assert.equal(h.host.metrics().below, 100);
  assert.equal(h.snapshot().jumpVisible, false, "only 100px of real content is hidden");
  h.grow("b2", 100);
  h.layout();
  assert.equal(h.snapshot().jumpVisible, true);
});

test("an input that cannot leave the live edge pauses nothing", () => {
  const h = landed();
  h.controller.input("wheel", 100);
  h.controller.input("keyboard", 1);
  h.controller.input("touch", 30);
  assert.equal(h.snapshot().following, true, "scrolling down at the bottom is not leaving it");
  const top = scrollHarness({ blocks: column(10) });
  top.controller.input("wheel", -100);
  assert.equal(top.snapshot().following, true, "scrolling up at the very top has nowhere to go");
  h.controller.input("wheel", 0);
  assert.equal(h.snapshot().following, true);
});

test("a downward input at the live edge resumes a reader who paused there", () => {
  const h = landed();
  h.controller.input("wheel", -10);
  assert.equal(h.snapshot().following, false);
  h.controller.input("wheel", 10); // the position is still within the edge threshold
  assert.equal(h.snapshot().following, true);
});

test("programmatic scrolls are never mistaken for the reader", () => {
  const h = landed();
  h.grow("b9", 400);
  h.layout(); // follows: a programmatic scroll
  h.controller.scrolled(); // the scroll event it produced
  assert.equal(h.snapshot().following, true);
  assert.equal(h.snapshot().pausedBy, null);
  // A page that shrinks clamps the position by itself.
  h.settle();
  h.grow("b9", -600);
  h.state.top = h.maxTop();
  h.controller.scrolled();
  assert.equal(h.snapshot().following, true, "a clamp is not the reader");
});

test("an upward scroll nobody caused pauses following, but a bounce at the edge does not", () => {
  const h = landed();
  h.state.top -= 10;
  h.state.time += 1; h.controller.scrolled();
  assert.equal(h.snapshot().following, true, "a bounce back to the edge is not leaving it");
  h.userScroll(h.maxTop() - 600); // focus scrolled something into view
  assert.equal(h.snapshot().following, false);
  assert.equal(h.snapshot().pausedBy, "scroll");
});

test("a scroll inside the window of our own scroll is not the reader's", () => {
  const h = landed();
  pausedAt(h, 300);
  h.controller.jump();
  h.state.top = 100; // an intermediate frame of the smooth scroll, far from the edge
  h.controller.scrolled();
  assert.equal(h.snapshot().following, true);
});

test("resume: only a downward scroll that ends at the live edge, and not while selecting or dragging the scrollbar", () => {
  const h = landed();
  pausedAt(h, 800);
  assert.equal(h.snapshot().following, false);
  h.userScroll(1200);
  assert.equal(h.snapshot().following, false, "closer but not at the edge");
  h.userScroll(h.maxTop() - 10);
  assert.equal(h.snapshot().following, true, "within the threshold, moving down");
  assert.equal(h.snapshot().pausedBy, null);
  // Moving up within the threshold is not a return.
  h.controller.input("wheel", -5);
  h.userScroll(h.maxTop() - 12);
  assert.equal(h.snapshot().following, false);
  // A selection keeps it paused even at the edge, until the selection goes.
  h.controller.selection(true);
  h.userScroll(h.maxTop() - 40);
  h.userScroll(h.maxTop());
  assert.equal(h.snapshot().following, false);
  h.controller.selection(false);
  assert.equal(h.snapshot().following, true, "the selection ended at the edge");
});

test("a scrollbar drag keeps following paused until the pointer is released at the edge", () => {
  const h = landed();
  h.controller.scrollbarDown();
  h.userScroll(h.maxTop());
  assert.equal(h.snapshot().following, false, "still dragging");
  h.controller.pointerUp();
  assert.equal(h.snapshot().following, true);
  const away = landed();
  away.controller.scrollbarDown();
  away.userScroll(300);
  away.controller.pointerUp();
  assert.equal(away.snapshot().following, false, "released far from the edge");
});

test("jump resumes following and scrolls smoothly to the end (instantly with reduced motion)", () => {
  const h = landed();
  pausedAt(h, 400);
  assert.equal(h.snapshot().jumpVisible, true);
  h.controller.jump();
  assert.equal(h.snapshot().following, true);
  assert.equal(h.snapshot().jumpVisible, false);
  const last = h.state.scrolls.at(-1);
  assert.equal(last.behavior, "smooth");
  const calm = landed({ reducedMotion: true });
  pausedAt(calm, 400);
  calm.controller.jump();
  assert.equal(calm.state.scrolls.at(-1).behavior, "auto");
});

test("a smooth jump is not cut short by layout passes and catches up with growth after it lands", () => {
  const h = landed();
  pausedAt(h, 400);
  h.controller.jump();
  const count = h.state.scrolls.length;
  h.state.top = 700; // mid-flight
  h.grow("b9", 100);
  h.layout();
  assert.equal(h.state.scrolls.length, count, "the smooth scroll is left alone");
  h.tick(1000); // it landed; the controller looks again
  assert.equal(h.state.scrolls.length > count, true);
  assert.equal(h.host.metrics().below <= 0, true);
});

test("after a smooth jump stops, growth that came meanwhile is caught up with at once, not after the whole window", () => {
  const h = landed();
  pausedAt(h, 400);
  h.controller.jump();
  h.state.top = h.maxTop() - 40; // landed a little short: the last row finished growing while it scrolled
  h.state.time += 16;
  h.controller.scrolled();
  h.grow("b9", 40);
  h.tick(200); // far less than the 900ms window
  assert.equal(h.host.metrics().below <= 0, true);
});

test("a new turn is pinned near the top and followed only once its content passes the bottom", () => {
  const h = landed();
  h.add({ id: "turn", h: 60 });
  h.state.turn = "turn";
  h.state.room = 460;
  h.controller.beginTurn();
  h.tick();
  const pin = h.state.scrolls.at(-1);
  assert.equal(pin.behavior, "smooth");
  assert.equal(pin.top, h.y("turn") - 20, "20px gap above the turn");
  assert.equal(h.snapshot().following, true);
  // Growth that still fits under the pinned turn does not move the view.
  h.settle();
  const count = h.state.scrolls.length;
  h.grow("turn", 100);
  h.layout();
  assert.equal(h.state.scrolls.length, count);
  // Once it outgrows the view, the view follows.
  h.grow("turn", 700);
  h.layout();
  assert.equal(h.host.metrics().below <= 0, true);
});

test("beginTurn resumes following for a reader who had scrolled away", () => {
  const h = landed();
  pausedAt(h, 300);
  h.add({ id: "turn", h: 60 });
  h.state.turn = "turn";
  h.controller.beginTurn();
  assert.equal(h.snapshot().following, true);
  h.tick();
  assert.equal(h.state.scrolls.at(-1).top, h.y("turn") - 20);
});

/** A long conversation with a message just sent beneath it, ready for `beginTurn`. */
function sent(extra = {}) {
  const h = landed(extra);
  h.add({ id: "turn", h: 60 });
  h.state.turn = "turn";
  h.state.room = 460;
  return h;
}

test("a pin cut short is finished, never replaced by following the live edge", () => {
  const h = sent();
  h.controller.beginTurn();
  h.tick();
  const target = h.y("turn") - 20;
  assert.deepEqual(h.state.scrolls.at(-1), { top: target, behavior: "smooth" });
  // Something (a focus change, a layout shift) cancelled the smooth scroll after a few pixels, leaving the new message under the composer.
  h.state.top = 1520;
  h.tick(1000); // the smooth window closes and the controller looks again
  assert.deepEqual(h.state.scrolls.at(-1), { top: target, behavior: "auto" }, "finished at once, at the turn, not at the live edge");
  assert.equal(h.state.top, target);
  // Landed: the pin is let go, and later growth is a plain follow.
  h.tick();
  const count = h.state.scrolls.length;
  h.grow("turn", 100);
  h.layout();
  assert.equal(h.state.scrolls.length, count, "content that still fits under the pinned turn moves nothing");
});

test("a smooth pin that stops short is noticed when its scroll events go quiet, not after the whole window", () => {
  const h = sent();
  h.controller.beginTurn();
  h.tick();
  const target = h.y("turn") - 20;
  h.state.top = 1700; // the animation ran a little, then was cut off
  h.state.time += 16;
  h.controller.scrolled();
  h.tick(200); // far less than the 900ms smooth window
  assert.deepEqual(h.state.scrolls.at(-1), { top: target, behavior: "auto" });
  assert.equal(h.state.top, target);
});

test("a smooth pin that landed lets following start without waiting out the window", () => {
  const h = sent();
  h.controller.beginTurn();
  h.tick();
  h.controller.scrolled(); // the animation's last scroll event
  h.tick(200);
  h.tick(); // the pin is checked, found landed and let go
  h.grow("turn", 800); // the reply outgrew the room, well inside the 900ms window
  h.layout();
  assert.equal(h.host.metrics().below <= 0, true);
});

test("a layout pass during the smooth pin leaves it alone, and nothing hides the turn behind the composer in between", () => {
  const h = sent();
  h.controller.beginTurn();
  h.tick();
  const count = h.state.scrolls.length;
  h.state.top = 1700; // mid-flight
  h.grow("turn", 30);
  h.layout();
  assert.equal(h.state.scrolls.length, count);
});

test("the reader's own input ends a pending pin", () => {
  const h = sent();
  h.controller.beginTurn();
  h.tick();
  h.controller.input("wheel", -120);
  h.userScroll(1200);
  const count = h.state.scrolls.length;
  h.tick(1000);
  h.layout();
  assert.equal(h.state.scrolls.length, count, "the reader is not dragged back to the turn");
  assert.equal(h.snapshot().following, false);
});

test("the pin waits for the room under the turn to be written before scrolling", () => {
  const h = sent();
  h.state.composer = 200; // the room changes with the composer, so this pass writes a new one
  h.controller.beginTurn();
  const count = h.state.scrolls.length;
  h.tick();
  assert.equal(h.state.scrolls.length, count, "scrolling now would be clamped short of the top");
  h.tick();
  assert.equal(h.state.scrolls.length > count, true);
  assert.equal(h.state.scrolls.at(-1).top, h.y("turn") - 20);
});

test("a page too short to put the turn at the top pins as far as it can, then follows", () => {
  const h = landed();
  h.add({ id: "turn", h: 60 });
  h.state.turn = "turn";
  h.state.room = 0; // nothing reserved under it
  h.controller.beginTurn();
  h.tick();
  h.tick();
  h.settle();
  assert.equal(h.state.top, h.maxTop());
  h.grow("turn", 400);
  h.layout();
  assert.equal(h.host.metrics().below <= 0, true, "the pin did not stay behind to block following");
});

test("with no turn to pin, following carries on", () => {
  const h = landed();
  h.controller.beginTurn();
  h.tick();
  h.tick();
  h.grow("b9", 300);
  h.layout();
  assert.equal(h.host.metrics().below <= 0, true);
});

test("opening a conversation with a running turn starts that turn at the top instantly", () => {
  const h = scrollHarness({ blocks: column(10), turn: "b8" });
  h.controller.landOnLatest({ turn: true });
  h.tick(); // the room under the turn is written first
  h.tick();
  assert.deepEqual(h.state.scrolls.at(-1), { top: h.y("b8") - 20, behavior: "auto" });
});

test("reduced motion pins a new turn without smooth scrolling", () => {
  const h = scrollHarness({ blocks: column(10), turn: "b8", reducedMotion: true });
  h.controller.beginTurn();
  h.tick();
  h.tick();
  assert.equal(h.state.scrolls.at(-1).behavior, "auto");
});

test("the reading anchor survives content inserted above the view (earlier messages loading)", () => {
  const h = landed();
  pausedAt(h, 700);
  const anchor = h.host.captureAnchor();
  h.controller.holdPlace();
  h.insertAbove("b0", { id: "old1", h: 300 });
  h.insertAbove("b0", { id: "old2", h: 500 });
  h.layout();
  const shift = h.host.anchorShift(anchor);
  assert.equal(shift, 0, "the first visible block is exactly where it was");
  assert.equal(h.state.top, 700 + 800);
  assert.equal(h.snapshot().pausedBy, "history");
});

test("the reading anchor survives a disclosure above the viewport changing height, with no reader scroll in between", () => {
  const h = landed();
  pausedAt(h, 1000);
  const anchor = h.host.captureAnchor();
  h.grow("b1", 240); // a disclosure far above opened
  h.layout();
  assert.equal(h.host.anchorShift(anchor), 0);
  h.grow("b1", -240);
  h.layout();
  assert.equal(h.host.anchorShift(anchor), 0, "and closed again");
});

test("a toggle keeps its own place, not the first visible element's", () => {
  const h = landed();
  pausedAt(h, 1000);
  const toggle = { id: "b6", offset: h.y("b6") - h.state.top }; // inside the view, below the first visible block
  h.controller.toggle(toggle);
  h.grow("b5", 150); // something above the toggle changed height as it opened
  h.layout();
  assert.equal(h.host.anchorShift(toggle), 0);
  const top = h.state.top;
  h.grow("b8", 400); // details loading below: nothing moves
  h.layout();
  assert.equal(h.state.top, top);
});

test("a toggle that collapses back to the live edge resumes following", () => {
  const h = landed();
  h.grow("b9", 600);
  h.layout();
  h.controller.toggle({ id: "b9", offset: 0 });
  h.grow("b9", -600);
  h.state.top = h.maxTop();
  h.layout();
  assert.equal(h.snapshot().following, true);
});

test("a reader scroll right after a correction is not undone", () => {
  const h = landed();
  pausedAt(h, 900);
  h.grow("b0", 200);
  h.layout(); // corrected programmatically
  h.state.top += 150; // the reader keeps scrolling within the programmatic window
  h.state.time += 5;
  h.controller.scrolled();
  h.layout();
  assert.equal(h.host.anchorShift(h.host.captureAnchor()), 0);
  const anchoredAt = h.host.captureAnchor();
  assert.equal(h.state.top, 900 + 200 + 150, "the reader's own movement stands");
  assert.equal(anchoredAt !== null, true);
});

test("a vanished anchor leaves the position alone", () => {
  const h = landed();
  pausedAt(h, 700);
  const top = h.state.top;
  h.state.blocks = h.state.blocks.filter((block) => block.id !== "b3");
  h.layout();
  assert.equal(h.state.top <= top, true);
});

test("composer growth while following keeps the end of the content above it; while paused it moves nothing", () => {
  const h = landed();
  h.state.composer = 220; // the textarea grew
  h.layout();
  assert.equal(h.host.metrics().below <= 0, true);
  const paused = landed();
  pausedAt(paused, 800);
  const top = paused.state.top;
  paused.state.composer = 220;
  paused.layout();
  assert.equal(paused.state.top, top);
});

test("subscribers hear only about real changes", () => {
  const h = landed();
  let heard = 0;
  const stop = h.controller.subscribe(() => heard++);
  h.layout(); h.layout();
  assert.equal(heard, 0);
  h.controller.input("wheel", -50);
  assert.equal(heard, 1);
  h.controller.input("wheel", -50);
  assert.equal(heard, 1, "already paused for the same reason");
  stop();
  h.controller.jump();
  assert.equal(heard, 1);
});

test("requestPass coalesces into one frame and cancel drops pending work", () => {
  const h = landed();
  h.grow("b9", 200);
  h.controller.requestPass(); h.controller.requestPass(); h.controller.requestPass();
  assert.equal(h.state.frames.filter(Boolean).length, 1);
  h.frame();
  assert.equal(h.host.metrics().below <= 0, true);
  h.grow("b9", 200);
  h.controller.requestPass();
  h.controller.cancel();
  h.frame();
  assert.equal(h.host.metrics().below > 0, true, "the dropped frame did nothing");
  h.controller.requestPass();
  h.frame();
  assert.equal(h.host.metrics().below <= 0, true, "and the controller still works afterwards");
});

test("cancel clears a smooth-scroll catch-up timer", () => {
  const h = landed();
  pausedAt(h, 300);
  h.controller.jump();
  assert.equal(h.state.timers.some((timer) => timer.live), true);
  h.controller.cancel();
  assert.equal(h.state.timers.filter((timer) => timer.live).length, 0);
});

test("releasing a pointer that never grabbed the scrollbar resumes nothing", () => {
  const h = landed();
  h.controller.input("wheel", -5);
  h.controller.pointerUp();
  assert.equal(h.snapshot().following, false);
});

test("selection transitions are idempotent", () => {
  const h = landed();
  h.controller.selection(true);
  h.controller.selection(true);
  h.controller.selection(false);
  h.controller.selection(false);
  assert.equal(h.snapshot().following, true, "ended at the live edge");
});

test("a fold above the reader shifts the view with it, keeping following and the reader's place", () => {
  // Following at the live edge: the steps (b8) above the answer (b9) fold away by 570px.
  const h = landed();
  h.grow("b8", 600);
  h.layout();
  h.settle();
  const before = h.y("b9") - h.state.top;
  h.grow("b8", -570);
  h.controller.shift(-570);
  assert.equal(h.y("b9") - h.state.top, before, "the answer stays where it was on screen");
  h.tick();
  h.layout();
  assert.equal(h.y("b9") - h.state.top, before, "and the next layout pass leaves it there");
  assert.equal(h.snapshot().following, true);

  // Paused, reading the answer: the remembered place is the shifted one, so the pass does not undo the shift.
  const p = landed();
  pausedAt(p, 900);
  const reading = p.y("b9") - p.state.top;
  p.grow("b8", -40);
  p.controller.shift(-40);
  p.layout();
  p.tick();
  assert.equal(p.y("b9") - p.state.top, reading);
  assert.equal(p.snapshot().following, false);
  p.settle();
  assert.equal(p.snapshot().following, false, "our own scroll never counts as the reader's");
});

/**
 * A host closer to the real page than the harness: the room is the latest turn's min-height (written a frame late, as
 * lib/scroll.ts does), the composer is in flow under it, and the browser clamps scrollTop whenever the page gets shorter.
 */
function clampingPage() {
  const s = { top: 0, viewport: 800, composer: 140, earlier: 3000, turnContent: 120, room: 0, time: 1e6, frames: [] };
  const turnTop = () => 64 + s.earlier + 24;
  const height = () => turnTop() + Math.max(s.turnContent, s.room) + 24 + s.composer;
  const clamp = () => { s.top = Math.min(s.top, Math.max(0, height() - s.viewport)); };
  const host = {
    metrics: () => ({ top: s.top, height: height(), viewport: s.viewport, composer: s.composer, below: turnTop() + s.turnContent - (s.top + s.viewport - s.composer) }),
    scrollTo(top) { s.top = Math.max(0, Math.min(top, height() - s.viewport)); },
    captureAnchor: () => null, anchorShift: () => null,
    turnTop: (gap) => turnTop() - gap,
    setRoom(px) { s.frames.push(() => { s.room = px; clamp(); controller.layoutChanged(); }); },
    reducedMotion: () => true,
  };
  const controller = createScrollController({ host, now: () => s.time, requestFrame: (callback) => { s.frames.push(callback); return s.frames.length; }, cancelFrame() {}, setTimeout: () => 0, clearTimeout() {} });
  const frames = (count = 3) => { for (let i = 0; i < count; i++) s.frames.splice(0).forEach((callback) => callback()); };
  const change = (patch) => { Object.assign(s, patch); clamp(); controller.layoutChanged(); frames(); };
  return { s, controller, frames, change, gap: () => turnTop() - s.top };
}

test("a pinned turn stays at the top when the composer shrinks, the view grows or text above re-wraps", () => {
  const page = clampingPage();
  page.controller.landOnLatest({ turn: false });
  page.frames(2);
  page.controller.beginTurn();
  page.frames(4);
  page.s.time += 5000;
  page.frames(1);
  assert.equal(page.gap(), 20, "pinned with its gap");
  page.change({ composer: page.s.composer - 48 }); // the sent message's lines leave the composer
  assert.equal(page.gap(), 20, "a shorter composer does not slide the turn down");
  page.change({ viewport: page.s.viewport + 300 }); // a phone keyboard closes
  assert.equal(page.gap(), 20, "a taller view does not slide the turn down");
  page.change({ earlier: page.s.earlier + 400 }); // earlier messages re-wrap (a panel opened beside them)
  assert.equal(page.gap(), 20, "text above re-wrapping does not move the turn");
  page.change({ turnContent: 2000 }); // the reply outgrows the room: the live edge is followed from here
  assert.equal(page.controller.getSnapshot().following, true);
  assert.ok(page.gap() < 0, "the view follows the reply past the top of the turn");
});
