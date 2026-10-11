import assert from "node:assert/strict";
import { test } from "node:test";
import { COPIED_MS, FAILED_MS, copyLabels, copyToClipboard } from "../features/sessions/copy-feedback.ts";

test("idle keeps the caller's label and announces nothing", () => {
  assert.deepEqual(copyLabels("idle", "Copy code"), { name: "Copy code", visible: "Copy code", status: "", clearAfter: null });
});

test("success is unchanged: Copied, announced, cleared after a moment", () => {
  assert.deepEqual(copyLabels("copied", "Copy"), { name: "Copied", visible: "Copied", status: "Copied to clipboard", clearAfter: COPIED_MS });
});

test("failure is stated in words, announced, and clears on its own (a little later, so it can be read)", () => {
  const failed = copyLabels("failed", "Copy");
  assert.equal(failed.name, "Couldn't copy");
  assert.equal(failed.visible, "Couldn't copy");
  assert.equal(failed.status, "Couldn't copy to the clipboard");
  assert.equal(failed.clearAfter, FAILED_MS);
  assert.ok(FAILED_MS > COPIED_MS);
});

test("copying reports success, a refusal and a missing clipboard without throwing", async () => {
  const written = [];
  assert.equal(await copyToClipboard("hello", { writeText: async (text) => { written.push(text); } }), true);
  assert.deepEqual(written, ["hello"]);
  assert.equal(await copyToClipboard("x", { writeText: async () => { throw new DOMException("denied", "NotAllowedError"); } }), false);
  assert.equal(await copyToClipboard("x", { writeText: () => { throw new Error("sync throw"); } }), false);
  assert.equal(await copyToClipboard("x", undefined), false);
  assert.equal(await copyToClipboard("x", null), false);
  assert.equal(await copyToClipboard("x", {}), false);
});
