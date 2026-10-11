import { test } from "node:test";
import assert from "node:assert/strict";
import { conversationOrder, sameSession, upsertSession } from "../lib/conversation-order.ts";

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

test("a read that changes nothing keeps the same session list, so the workspace does not re-render", () => {
  const base = { id: "a", project_id: "p", title: "One", mode: "general", repository: null, archived: false, active_task_id: null, continuation_task_id: null, last_sequence: 4, created_at: "2026-01-01T00:00:00Z", updated_at: "2026-01-02T00:00:00Z", revision: 3, status: "idle" };
  const other = { ...base, id: "b", updated_at: "2026-01-01T00:00:00Z" };
  const list = [base, other];
  assert.equal(sameSession(base, { ...base }), true);
  assert.equal(upsertSession(list, { ...base }), list);
  assert.equal(upsertSession(list, { ...base, revision: 2, title: "Older" }), list, "an older read never undoes a newer one");
  const renamed = upsertSession(list, { ...base, revision: 4, title: "Renamed" });
  assert.notEqual(renamed, list);
  assert.equal(renamed[0].title, "Renamed");
  assert.equal(sameSession(base, { ...base, status: "running" }), false);
  assert.equal(sameSession(base, { ...base, repository: { repo_id: "o/r", ref: "HEAD" } }), false);
  const moved = upsertSession(list, { ...other, revision: 4, updated_at: "2026-01-03T00:00:00Z" });
  assert.deepEqual(moved.map((item) => item.id), ["b", "a"]);
  const added = upsertSession(list, { ...base, id: "c", updated_at: "2026-01-04T00:00:00Z" });
  assert.deepEqual(added.map((item) => item.id), ["c", "a", "b"]);
});
