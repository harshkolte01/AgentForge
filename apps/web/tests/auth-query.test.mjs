import { test } from "node:test";
import assert from "node:assert/strict";
import { authQuery } from "../features/auth/auth-query.ts";

test("an empty query asks for nothing", () => {
  assert.deepEqual(authQuery({}), { next: null, authError: null, reason: null });
});

test("a safe destination, a callback code and the expiry reason are carried", () => {
  assert.deepEqual(authQuery({ next: "/chat/abc", auth_error: "denied", reason: "expired" }), { next: "/chat/abc", authError: "denied", reason: "expired" });
});

test("hostile or repeated values are dropped or narrowed", () => {
  assert.equal(authQuery({ next: "https://evil.example" }).next, null);
  assert.equal(authQuery({ next: ["/settings", "//evil.example"] }).next, "/settings");
  assert.equal(authQuery({ next: "/sign-in" }).next, null);
  assert.equal(authQuery({ reason: "anything-else" }).reason, null);
  assert.equal(authQuery({ auth_error: "x".repeat(500) }).authError.length, 64);
  assert.equal(authQuery({ auth_error: "" }).authError, null);
});
