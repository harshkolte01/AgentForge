/**
 * The repositories the person's GitHub App installations grant, read at most once a minute for the whole page. Listing them walks
 * every installation on GitHub, so pickers opening, windows regaining focus and Settings share one copy instead of each asking.
 * A failed read is never cached; `invalidate()` (Retry, a fresh install) makes the next read go to the server.
 */
import { api } from "@/lib/task-api";
import type { GitHubRepository } from "@/lib/github-api";

export const REPOSITORIES_TTL_MS = 60_000;

export type RepositoryCache = {
  /** The cached list while it is fresh, else a read (shared with any read already in flight). `force` always reads. */
  get(options?: { force?: boolean }): Promise<GitHubRepository[]>;
  /** The last list read, fresh or not, for showing at once while a newer read runs. */
  peek(): GitHubRepository[] | null;
  /** Whether the cached list is younger than the time to live. */
  fresh(): boolean;
  invalidate(): void;
};

/** `refresh` asks the server for a new read of GitHub rather than the copy it keeps for a few seconds. */
export type RepositoryLoad = (options: { refresh: boolean }) => Promise<GitHubRepository[]>;

export function createRepositoryCache(deps: { load: RepositoryLoad; now: () => number; ttlMs?: number }): RepositoryCache {
  const ttl = deps.ttlMs ?? REPOSITORIES_TTL_MS;
  let entry: { value: GitHubRepository[]; at: number } | null = null;
  let inflight: Promise<GitHubRepository[]> | null = null;
  // Bumped by invalidate and forced reads, so an older read finishing late never overwrites a newer answer.
  let generation = 0;
  // After `invalidate` the person is asking for what GitHub says now (Retry, a fresh install), not a recent copy.
  let refresh = false;
  const fresh = () => !!entry && deps.now() - entry.at < ttl;
  return {
    get({ force = false } = {}) {
      if (!force && fresh() && entry) return Promise.resolve(entry.value);
      if (!force && inflight) return inflight;
      const mine = ++generation;
      const bypass = force || refresh;
      refresh = false;
      const read = deps.load({ refresh: bypass }).then((value) => {
        if (mine === generation) entry = { value, at: deps.now() };
        return value;
      }).finally(() => { if (inflight === read) inflight = null; });
      inflight = read;
      return read;
    },
    peek: () => entry?.value ?? null,
    fresh,
    invalidate() { generation++; entry = null; inflight = null; refresh = true; },
  };
}

export const repositoryCache = createRepositoryCache({
  load: ({ refresh }) => api<{ repositories: GitHubRepository[] }>(`/github/repositories${refresh ? "?refresh=true" : ""}`).then((value) => value.repositories),
  now: () => Date.now(),
});
