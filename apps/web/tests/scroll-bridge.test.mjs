import assert from "node:assert/strict";
import { test } from "node:test";
import { createScrollerBridge } from "../lib/scroll.ts";

/** Just enough of an element for the DOM host to measure and scroll. */
function scroller(name, { top = 0, height = 1000, viewport = 500 } = {}) {
  return { name, scrollTop: top, scrollHeight: height, clientHeight: viewport, calls: [], lastElementChild: null,
    querySelector: () => null, querySelectorAll: () => [], getBoundingClientRect: () => ({ top: 0, bottom: viewport, right: 100 }),
    scrollTo(options) { this.calls.push(options); } };
}

test("with no scroller mounted every call is a harmless no-op", () => {
  const { host } = createScrollerBridge();
  assert.deepEqual(host.metrics(), { top: 0, height: 0, viewport: 0, below: 0, composer: 0 });
  assert.equal(host.captureAnchor(), null);
  assert.equal(host.turnTop(20), null);
  assert.equal(host.anchorShift({}), null);
  host.scrollTo(100, "auto");
  host.setRoom(10);
});

test("the host follows the mounted scroller, and a replacement takes over from the one that went away", () => {
  const { host, mount } = createScrollerBridge();
  const first = scroller("first", { top: 300 });
  const second = scroller("second", { top: 40, height: 2000, viewport: 600 });
  const unmountFirst = mount(first);
  assert.equal(host.metrics().top, 300);
  host.scrollTo(450, "smooth");
  assert.deepEqual(first.calls, [{ top: 450, behavior: "smooth" }]);

  // The subtree holding the scroller was replaced: the new element is mounted before the old one lets go.
  const unmountSecond = mount(second);
  assert.deepEqual([host.metrics().top, host.metrics().height, host.metrics().viewport], [40, 2000, 600]);
  host.scrollTo(80, "auto");
  assert.deepEqual(second.calls, [{ top: 80, behavior: "auto" }]);
  assert.equal(first.calls.length, 1, "the element that went away is never scrolled again");

  unmountFirst();
  assert.equal(host.metrics().top, 40, "letting go of the old scroller does not unmount its replacement");
  unmountSecond();
  assert.equal(host.metrics().viewport, 0);
});
