import { test } from "node:test";
import assert from "node:assert/strict";
import { authPath, githubStartPath, safeNextPath } from "../lib/auth-redirect.ts";
import { authErrorMessage } from "../lib/auth-errors.ts";

test("only same-origin paths are followed after sign-in", () => {
  assert.equal(safeNextPath("/chat/0b9c7a10-1d3e-4d6f-9a43-2f5b1c6d7e88"), "/chat/0b9c7a10-1d3e-4d6f-9a43-2f5b1c6d7e88");
  assert.equal(safeNextPath("/settings?section=github"), "/settings?section=github");
  for (const hostile of ["https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "chat", "", null, undefined, "/ spaced", "/tab\t", "/del\u007f"]) {
    assert.equal(safeNextPath(hostile), null, String(hostile));
  }
  assert.equal(safeNextPath(`/${"a".repeat(512)}`), null);
});

test("the sign-in pages are never a destination", () => {
  for (const page of ["/sign-in", "/sign-up", "/sign-in?next=/chat", "/sign-up/", "/sign-in#top"]) assert.equal(safeNextPath(page), null, page);
  assert.equal(safeNextPath("/sign-inventory"), "/sign-inventory");
});

test("sign-in links carry a safe destination and nothing else", () => {
  assert.equal(authPath("/sign-in"), "/sign-in");
  assert.equal(authPath("/sign-in", "/chat"), "/sign-in");
  assert.equal(authPath("/sign-up", "//evil.example"), "/sign-up");
  assert.equal(authPath("/sign-in", "/settings?section=github"), "/sign-in?next=%2Fsettings%3Fsection%3Dgithub");
  assert.equal(authPath("/sign-in", "/chat/x", { reason: "expired", auth_error: "denied" }), "/sign-in?next=%2Fchat%2Fx&reason=expired&auth_error=denied");
});

test("GitHub sign-in starts at the API with the same destination rule", () => {
  assert.equal(githubStartPath(), "/api/v1/auth/github/start");
  assert.equal(githubStartPath("/chat"), "/api/v1/auth/github/start");
  assert.equal(githubStartPath("https://evil.example"), "/api/v1/auth/github/start");
  assert.equal(githubStartPath("/settings?section=github"), "/api/v1/auth/github/start?next=%2Fsettings%3Fsection%3Dgithub");
});

test("every callback code has a sentence, and unknown codes get a safe one", () => {
  assert.match(authErrorMessage("signup_closed"), /not open/);
  assert.match(authErrorMessage("denied"), /cancelled/);
  assert.match(authErrorMessage("<script>"), /could not be completed/);
});
