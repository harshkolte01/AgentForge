import { test } from "node:test";
import assert from "node:assert/strict";
import { conversationOrder } from "../lib/conversation-order.ts";

test("queued intake never displaces an active turn or pairs a reply by adjacency", () => {
  const messages = [
    { id: "a-user", turn_id: "a", sequence: 1, role: "user", turn_state: "completed" },
    { id: "b-user", turn_id: "b", sequence: 2, role: "user", turn_state: "running" },
    { id: "c-user", turn_id: "c", sequence: 3, role: "user", turn_state: "queued" },
    { id: "a-answer", turn_id: "a", sequence: 4, role: "assistant", turn_state: "completed" },
  ];
  assert.deepEqual(conversationOrder(messages).map((m) => m.id), ["a-user", "a-answer", "b-user"]);
  assert.deepEqual(messages.map((m) => m.sequence), [1, 2, 3, 4]);
});

test("blocked messages stay queued and cancelled requests remain visible", () => {
  const messages = [
    { id: "blocked", sequence: 1, role: "user", turn_state: "blocked" },
    { id: "cancelled", sequence: 2, role: "user", turn_state: "cancelled" },
    { id: "legacy", sequence: 3, role: "assistant" },
  ];
  assert.deepEqual(conversationOrder(messages).map((m) => m.id), ["cancelled", "legacy"]);
});
