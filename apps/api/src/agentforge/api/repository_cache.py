"""A short-lived, per-user copy of the repository list, so the picker does not walk GitHub on every open."""
import asyncio
import time
from collections import OrderedDict


class RepositoryListCache:
    """Listing a user's repositories walks every installation on GitHub. Pickers opening in several tabs, windows
    regaining focus and Settings all ask for the same list, so one read per `ttl` seconds serves them all, and
    concurrent askers share the read already in flight. Only successful reads are kept. The list is for display:
    every run still checks its repository against GitHub itself (`AuthorizedGitHubClient`)."""

    def __init__(self, *, ttl=30.0, capacity=256, clock=time.monotonic):
        self.ttl, self.capacity, self._clock = ttl, capacity, clock
        self._entries = OrderedDict()   # key -> (valid until, value)
        self._pending = {}              # key -> task reading it now
        # Bumped by `clear`, so a read that began before it never stores what it found.
        self._generation = 0

    async def get(self, key, load, *, refresh=False):
        """The cached value while it is fresh, else the result of `load()` (shared with a read already in flight).
        `refresh` always reads anew. A caller that gives up does not cancel the read the others are waiting for."""
        if not refresh:
            hit = self._entries.get(key)
            if hit is not None and hit[0] > self._clock():
                self._entries.move_to_end(key)
                return hit[1]
            pending = self._pending.get(key)
            if pending is not None:
                return await asyncio.shield(pending)
        task = asyncio.ensure_future(self._read(key, load, self._generation))
        self._pending[key] = task
        # A read nobody is left to wait for must not report an unretrieved failure.
        task.add_done_callback(lambda done: done.cancelled() or done.exception())
        return await asyncio.shield(task)

    async def _read(self, key, load, generation):
        task = asyncio.current_task()
        try:
            value = await load()
            if generation == self._generation:
                self._entries[key] = (self._clock() + self.ttl, value)
                self._entries.move_to_end(key)
                while len(self._entries) > self.capacity:
                    self._entries.popitem(last=False)
            return value
        finally:
            if self._pending.get(key) is task:
                del self._pending[key]

    def clear(self):
        """Forget every list and stop sharing reads already in flight (a grant changed: the next ask reads anew)."""
        self._generation += 1
        self._entries.clear()
        self._pending.clear()
