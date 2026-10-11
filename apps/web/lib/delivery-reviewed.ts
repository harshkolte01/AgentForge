/**
 * "Reviewed" marks on changed files, kept in this browser only. A mark is a file's path plus the fingerprint of the diff that was
 * reviewed, per conversation, so editing the file again makes it unreviewed. Storage can be missing or full: every read and write
 * is guarded and the panel works the same without it.
 */
export type ReviewedMarks = Record<string, string>;
type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;

const keyFor = (sessionId: string) => `agentforge.delivery.reviewed.${sessionId}`;
const browserStorage = (): Store | null => { try { return typeof localStorage === "undefined" ? null : localStorage; } catch { return null; } };

export function loadReviewed(sessionId: string, storage: Store | null = browserStorage()): ReviewedMarks {
  try {
    const parsed: unknown = JSON.parse(storage?.getItem(keyFor(sessionId)) ?? "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return Object.fromEntries(Object.entries(parsed).filter((entry): entry is [string, string] => typeof entry[1] === "string"));
  } catch { return {}; }
}

/** Writes the marks that still match a file in `files` (path → current diff fingerprint), dropping the rest. */
export function saveReviewed(sessionId: string, marks: ReviewedMarks, files: ReadonlyMap<string, string> | null = null, storage: Store | null = browserStorage()): ReviewedMarks {
  const kept = files ? Object.fromEntries(Object.entries(marks).filter(([path, hash]) => files.get(path) === hash)) : marks;
  try {
    if (Object.keys(kept).length) storage?.setItem(keyFor(sessionId), JSON.stringify(kept));
    else storage?.removeItem(keyFor(sessionId));
  } catch { /* The marks then last for this visit only. */ }
  return kept;
}

export const isReviewed = (marks: ReviewedMarks, path: string, hash: string) => marks[path] === hash;
