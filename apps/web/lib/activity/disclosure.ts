/**
 * Remembers which disclosures a person opened or closed by hand, so a manual choice survives re-renders, the run
 * completing, the live row turning into the saved one, and navigating away and back within the page session.
 *
 * Only manual choices are stored. An entry nobody touched has no value (`get` returns undefined) and follows its own
 * default (open while the answer arrives in view, collapsed when loading history). Keys are `<taskId>:<entryId>`.
 *
 * Works with `useSyncExternalStore(store.subscribe, () => store.get(key), () => undefined)`: `get` returns a primitive, so
 * the snapshot is stable. State lives in module memory and is mirrored to `sessionStorage` when it is available (it can
 * be missing or throw in private windows and previews, in which case memory alone is used).
 */
export type StorageLike = { getItem(key: string): string | null; setItem(key: string, value: string): void };

export type DisclosureStore = {
  /** The person's choice for this key, or undefined when they never made one. */
  get(key: string): boolean | undefined;
  /** The person's choice, else `defaultOpen`. */
  resolve(key: string, defaultOpen: boolean): boolean;
  /** Records a manual choice. */
  set(key: string, open: boolean): void;
  /** Forgets one key, or every key that starts with `prefix` (for example a task's `"<taskId>:"`). */
  clear(prefix?: string): void;
  subscribe(listener: () => void): () => void;
};

/** The key for one disclosure of one task's activity. */
export const disclosureKey = (taskId: string, entryId: string): string => `${taskId}:${entryId}`;
/** The entry id of a task's whole "N actions" disclosure. */
export const ACTIVITY_DISCLOSURE = "activity";

type Options = { storageKey?: string; limit?: number };

function sessionStorageOrNull(): StorageLike | null {
  try { return typeof sessionStorage === "undefined" ? null : sessionStorage; } catch { return null; }
}

export function createDisclosureStore(storage: StorageLike | null = sessionStorageOrNull(), { storageKey = "agentforge:disclosure:v1", limit = 500 }: Options = {}): DisclosureStore {
  const choices = new Map<string, boolean>();
  const listeners = new Set<() => void>();
  let loaded = false;

  function load() {
    if (loaded) return;
    loaded = true;
    try {
      const saved: unknown = JSON.parse(storage?.getItem(storageKey) ?? "[]");
      if (Array.isArray(saved)) for (const item of saved) if (Array.isArray(item) && typeof item[0] === "string" && typeof item[1] === "boolean") choices.set(item[0], item[1]);
    } catch { /* A missing or damaged copy only means no remembered choices. */ }
  }
  function save() {
    try { storage?.setItem(storageKey, JSON.stringify([...choices])); } catch { /* Memory alone still remembers for this page. */ }
  }
  const notify = () => listeners.forEach((listener) => listener());

  return {
    get(key) { load(); return choices.get(key); },
    resolve(key, defaultOpen) { load(); return choices.get(key) ?? defaultOpen; },
    set(key, open) {
      load();
      if (choices.get(key) === open) return;
      choices.delete(key); // re-insert so the newest choices are the last to be evicted
      choices.set(key, open);
      while (choices.size > limit) choices.delete(choices.keys().next().value as string);
      save();
      notify();
    },
    clear(prefix) {
      load();
      const before = choices.size;
      for (const key of [...choices.keys()]) if (prefix === undefined || key.startsWith(prefix)) choices.delete(key);
      if (choices.size !== before) { save(); notify(); }
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
  };
}

/** The page-wide store used by the chat. */
export const disclosure: DisclosureStore = createDisclosureStore();
