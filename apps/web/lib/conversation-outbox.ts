import { attachmentGoal } from "@/lib/attachments";
/** Unresolved admission intents survive reload; no credentials are stored here. */
import { api, type SessionInfo } from "@/lib/task-api";
import type { ConversationSession, TurnInput, TurnResult } from "@/lib/workspace-api";

export type PendingTurn = { id: string; account: string; sessionId: string | null; draftId?: string; input: TurnInput; createdAt: number };
const maxBytes = 3 * 1024 * 1024;
const storeName = "intents";

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") { reject(new Error("Durable message storage is unavailable in this browser.")); return; }
    const request = indexedDB.open("agentforge-admission", 2);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(storeName)) request.result.createObjectStore(storeName, { keyPath: "id" });
      if (!request.result.objectStoreNames.contains("drafts")) request.result.createObjectStore("drafts", { keyPath: "id" });
    };
    request.onblocked = () => reject(new Error("Close an older AgentForge tab to upgrade durable message storage."));
    request.onsuccess = () => { request.result.onversionchange = () => request.result.close(); resolve(request.result); };
    request.onerror = () => reject(new Error("Could not open durable message storage."));
  });
}

async function records(): Promise<PendingTurn[]> {
  const db = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const request = db.transaction(storeName).objectStore(storeName).getAll();
      request.onsuccess = () => resolve(request.result as PendingTurn[]);
      request.onerror = () => reject(new Error("Could not read pending messages."));
    });
  } finally { db.close(); }
}

async function persist(record: PendingTurn): Promise<void> {
  const db = await openDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(storeName, "readwrite");
      const store = transaction.objectStore(storeName);
      const request = store.getAll();
      request.onsuccess = () => {
        const existing = (request.result as PendingTurn[]).find((item) => item.id === record.id);
        if (existing && JSON.stringify(existing.input) !== JSON.stringify(record.input)) { transaction.abort(); return; }
        const others = (request.result as PendingTurn[]).filter((item) => item.id !== record.id);
        if (new TextEncoder().encode(JSON.stringify([...others, record])).length > maxBytes) { transaction.abort(); return; }
        store.put(record);
      };
      transaction.oncomplete = () => resolve();
      transaction.onerror = transaction.onabort = () => reject(new Error("Pending message storage is full or unavailable. Resolve pending sends before sending more."));
    });
  } finally { db.close(); }
}

export async function discardPending(id: string): Promise<void> {
  const db = await openDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction(storeName, "readwrite");
      transaction.objectStore(storeName).delete(id);
      transaction.oncomplete = () => resolve();
      transaction.onerror = transaction.onabort = () => reject(new Error("Could not acknowledge the saved message."));
    });
  } finally { db.close(); }
}

let cachedAccount: Promise<string> | null = null;
/**
 * The signed-in account's id. Read once and reused (drafts and pending sends are looked up on every conversation switch) until
 * the session changes: sign-in, sign-out and expiry call `forgetAccount`. `fresh` always asks the server, for checks that guard
 * a login boundary.
 */
export function accountId({ fresh = false }: { fresh?: boolean } = {}): Promise<string> {
  if (fresh || !cachedAccount) {
    const read = api<SessionInfo>("/auth/session").then((session) => {
      if (!session.authenticated || !session.user?.id) throw new Error("Sign in again before sending this message.");
      return session.user.id;
    });
    cachedAccount = read;
    read.catch(() => { if (cachedAccount === read) cachedAccount = null; });
  }
  return cachedAccount;
}
export function forgetAccount() { cachedAccount = null; }

export async function pendingTurns(account: string): Promise<PendingTurn[]> {
  return (await records()).filter((item) => item.account === account).sort((a, b) => a.createdAt - b.createdAt);
}

export async function saveIntent(account: string, sessionId: string | null, input: TurnInput): Promise<PendingTurn> {
  const id = `${account}:${input.idempotency_key}`;
  const previous = (await records()).find((item) => item.id === id);
  if (previous) {
    if (JSON.stringify(previous.input) !== JSON.stringify(input)) throw new Error("This retry differs from the saved message.");
    if (sessionId && previous.sessionId && sessionId !== previous.sessionId) throw new Error("This retry belongs to another conversation.");
    return previous;
  }
  const record = { id, account, sessionId, draftId: `${account}:${sessionId ?? "new"}`, input, createdAt: Date.now() };
  await persist(record);
  return record;
}

export async function deliverIntent(record: PendingTurn, signal?: AbortSignal): Promise<{ result: TurnResult; session: ConversationSession | null }> {
  // Recheck account before each delivery; replay never crosses a login boundary.
  if (await accountId({ fresh: true }) !== record.account) throw new Error("Sign in to the account that sent this message to retry it.");
  let session: ConversationSession | null = null;
  if (!record.sessionId) {
    session = await api<ConversationSession>("/sessions", { signal, method: "POST", body: JSON.stringify({
      idempotency_key: record.input.idempotency_key, title: record.input.goal.slice(0, 80), repository: record.input.repository, plugins: record.input.plugins }) });
    record = { ...record, sessionId: session.id };
    await persist(record);
  }
  // The plugin choice belongs to the conversation (sent above when it was created), not to a turn.
  const turn: Partial<TurnInput> = { ...record.input };
  delete turn.plugins;
  const result = await api<TurnResult>(`/sessions/${record.sessionId}/turns`, { signal, method: "POST", body: JSON.stringify(turn) });
  // The admission is authoritative. A later refresh failure cannot resend it as new work.
  const draftId = record.draftId ?? `${record.account}:${record.sessionId ?? "new"}`;
  const db = await openDatabase();
  let cleared = false;
  try {
    // A crash cannot retire the retry identity while leaving its old draft
    // inviting a fresh submission. Both changes commit together.
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction([storeName, "drafts"], "readwrite");
      const drafts = transaction.objectStore("drafts");
      const request = drafts.get(draftId);
      request.onsuccess = () => {
        const draft = request.result as ConversationDraft | undefined;
        if (draft && attachmentGoal(draft.goal, draft.attachments) === record.input.goal && JSON.stringify(draft.attachments) === JSON.stringify(record.input.attachments)) {
          cleared = true;
          drafts.put({ id: draftId, goal: "", attachments: [], revision: Math.max(Date.now(), draft.revision + 1) });
        }
        transaction.objectStore(storeName).delete(record.id);
      };
      transaction.oncomplete = () => resolve();
      transaction.onerror = transaction.onabort = () => reject(new Error("Message saved; its acknowledgement will retry safely."));
    });
  } finally { db.close(); }
  if (cleared) window.dispatchEvent(new CustomEvent("agentforge-turn-admitted", { detail: { draftId, input: record.input } }));
  return { result, session };
}

export type ConversationDraft = { id: string; goal: string; attachments: TurnInput["attachments"]; revision: number };

export async function readDraft(id: string): Promise<ConversationDraft | null> {
  const db = await openDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const request = db.transaction("drafts").objectStore("drafts").get(id);
      request.onsuccess = () => resolve(request.result ?? null);
      request.onerror = () => reject(new Error("Draft unavailable."));
    });
  } finally { db.close(); }
}

export async function writeDraft(draft: ConversationDraft): Promise<void> {
  const db = await openDatabase();
  try {
    await new Promise<void>((resolve, reject) => {
      const transaction = db.transaction("drafts", "readwrite");
      const store = transaction.objectStore("drafts");
      const previous = store.get(draft.id);
      previous.onsuccess = () => { if (!previous.result || previous.result.revision <= draft.revision) store.put(draft); };
      transaction.oncomplete = () => resolve();
      transaction.onerror = transaction.onabort = () => reject(new Error("Draft storage unavailable."));
    });
  } finally { db.close(); }
}
