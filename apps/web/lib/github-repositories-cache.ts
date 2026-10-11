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

export function createRepositoryCache(deps: { load: () => Promise<GitHubRepository[]>; now: () => number; ttlMs?: number }): RepositoryCache {
  const ttl = deps.ttlMs ?? REPOSITORIES_TTL_MS;
  let entry: { value: GitHubRepository[]; at: number } | null = null;
  let inflight: Promise<GitHubRepository[]> | null = null;
  // Bumped by invalidate and forced reads, so an older read finishing late never overwrites a newer answer.
  let generation = 0;
  const fresh = () => !!entry && deps.now() - entry.at < ttl;
  return {
    get({ force = false } = {}) {
      if (!force && fresh() && entry) return Promise.resolve(entry.value);
      if (!force && inflight) return inflight;
      const mine = ++generation;
      const read = deps.load().then((value) => {
        if (mine === generation) entry = { value, at: deps.now() };
        return value;
      }).finally(() => { if (inflight === read) inflight = null; });
      inflight = read;
      return read;
    },
    peek: () => entry?.value ?? null,
    fresh,
    invalidate() { generation++; entry = null; inflight = null; },
  };
}

export const repositoryCache = createRepositoryCache({
  load: () => api<{ repositories: GitHubRepository[] }>("/github/repositories").then((value) => value.repositories),
  now: () => Date.now(),
});
