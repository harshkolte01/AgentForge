import { beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { IDBFactory } from "fake-indexeddb";
import { accountId, deliverIntent, forgetAccount, pendingTurns, readDraft, saveIntent, writeDraft } from "../lib/conversation-outbox.ts";

const account = "fixture-account";
const input = () => ({ routing: "auto", repository: null, idempotency_key: crypto.randomUUID(), goal: "Keep the earlier decision.",
  allowed_web_hosts: [], attachments: [] });
let identity, lostCreation, lostTurn, creates, turns, calls;
const reply = (value) => new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  globalThis.indexedDB = new IDBFactory();
  globalThis.window = { dispatchEvent() {} };
  identity = account; lostCreation = lostTurn = false; creates = new Map(); turns = new Map(); calls = [];
  globalThis.fetch = async (url, options = {}) => {
    calls.push(url);
    if (url === "/api/v1/auth/session") return reply({ authenticated: true, user: { id: identity } });
    const body = JSON.parse(options.body);
    if (url === "/api/v1/sessions") {
      if (!creates.has(body.idempotency_key)) creates.set(body.idempotency_key, { id: crypto.randomUUID() });
      if (lostCreation) { lostCreation = false; throw new TypeError("Response lost after commit"); }
      return reply(creates.get(body.idempotency_key));
    }
    if (url.endsWith("/turns")) {
      if (!turns.has(body.idempotency_key)) turns.set(body.idempotency_key, { session_id: url.split("/")[4], turn_id: crypto.randomUUID(), state: "queued" });
      if (lostTurn) { lostTurn = false; throw new TypeError("Response lost after admission"); }
      return reply(turns.get(body.idempotency_key));
    }
    throw new Error(`Unexpected fixture request ${url}`);
  };
});

test("reload after lost turn receipt replays the same identity and clears its matching draft", async () => {
  const body = input();
  const intent = await saveIntent(account, null, body);
  await writeDraft({ id: `${account}:new`, goal: body.goal, attachments: [], revision: 1 });
  lostTurn = true;
  await assert.rejects(deliverIntent(intent));
  const [recovered] = await pendingTurns(account);
  assert.ok(recovered.sessionId);
  assert.equal(recovered.input.idempotency_key, body.idempotency_key);
  await deliverIntent(recovered);
  assert.equal(creates.size, 1);
  assert.equal(turns.size, 1);
  assert.deepEqual(await pendingTurns(account), []);
  assert.equal((await readDraft(`${account}:new`)).goal, "");
});

test("lost conversation-creation receipt retains its original creation identity", async () => {
  const intent = await saveIntent(account, null, input());
  lostCreation = true;
  await assert.rejects(deliverIntent(intent));
  await deliverIntent((await pendingTurns(account))[0]);
  assert.equal(creates.size, 1);
  assert.equal(turns.size, 1);
});

test("replay refuses an account change without posting any work", async () => {
  const intent = await saveIntent(account, "existing", input());
  identity = "different-account";
  await assert.rejects(deliverIntent(intent), /account/);
  assert.equal(calls.length, 1);
  assert.equal((await pendingTurns(account)).length, 1);
  assert.deepEqual(await pendingTurns(identity), []);
});

test("admission cannot clear a newer draft edited while recovery was running", async () => {
  const intent = await saveIntent(account, "existing", input());
  const id = `${account}:existing`;
  await writeDraft({ id, goal: "A newer unsent message", attachments: [], revision: 2 });
  await deliverIntent(intent);
  assert.equal((await readDraft(id)).goal, "A newer unsent message");
});

test("storage overflow rejects new work without evicting an unresolved intent", async () => {
  const first = await saveIntent(account, "existing", input());
  await assert.rejects(saveIntent(account, "existing", { ...input(), goal: "x".repeat(3 * 1024 * 1024) }), /full/);
  assert.deepEqual((await pendingTurns(account)).map((i) => i.id), [first.id]);
});

test("a reused local identity with changed payload is rejected", async () => {
  const body = input();
  await saveIntent(account, "existing", body);
  await assert.rejects(saveIntent(account, "existing", { ...body, goal: "Changed text" }), /differs/);
});

test("older asynchronous draft writes cannot restore an acknowledged draft", async () => {
  const id = `${account}:existing`;
  await writeDraft({ id, goal: "", attachments: [], revision: 100 });
  await writeDraft({ id, goal: "Old text", attachments: [], revision: 99 });
  assert.equal((await readDraft(id)).goal, "");
});

test("the account is read once and reused until the session changes; a delivery always checks it afresh", async () => {
  forgetAccount();
  assert.equal(await accountId(), account);
  assert.equal(await accountId(), account);
  assert.equal(calls.filter((url) => url === "/api/v1/auth/session").length, 1);
  identity = "different-account";
  assert.equal(await accountId(), account, "cached until forgotten");
  assert.equal(await accountId({ fresh: true }), "different-account");
  forgetAccount();
  identity = account;
  assert.equal(await accountId(), account);
  assert.equal(calls.filter((url) => url === "/api/v1/auth/session").length, 3);
});

test("a failed account read is not cached", async () => {
  forgetAccount();
  const working = globalThis.fetch;
  globalThis.fetch = async () => { throw new TypeError("offline"); };
  await assert.rejects(accountId());
  globalThis.fetch = working;
  assert.equal(await accountId(), account);
});
