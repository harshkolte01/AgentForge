"""What every web search provider has in common: the failure it raises and the answer it returns.

Providers (`exa_client.py`, `tavily_client.py`) never put a provider body, a prompt or an API key in a failure.
"""
import asyncio
import hashlib
import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal


class SearchFailure(ValueError):
    """Safe error code only; never includes provider bodies, prompts or API keys."""


class UnbilledSearchFailure(SearchFailure):
    """The provider refused the request before doing any work (bad key, no credit, rate limit, rejected request), so
    nothing was charged and the next provider can be tried without counting this attempt against the budget."""


@dataclass(frozen=True)
class SearchResponse:
    results: tuple[dict[str, str], ...]
    credits: int | None
    cost_usd: Decimal | None
    # Which provider answered ("exa", "tavily"). Empty for a response built without one.
    provider: str = ""


# --- several keys for one provider --------------------------------------------------------------------------------------
# A provider can be given several keys (`EXA_API_KEY=key1,key2`). A search uses the first key that works; when a key is
# refused for a reason that is about that key (expired or wrong, out of credit, rate limited) the next key serves the search and
# the refused key is left alone for a while. Anything else (a timeout, a server error, a rejected request) is not the key's
# fault, so it moves on to the other provider instead of trying every key against the same trouble.
logger = logging.getLogger(__name__)

MAX_KEYS = 20
_KEY_SEPARATORS = re.compile(r"[,;\s]+")
# Letters, digits and the punctuation API keys use (UUIDs, `tvly-…`, base64): a pasted quote or stray symbol is a mistake.
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9._~+/=:-]{8,512}$")

INVALID, EXHAUSTED, RATE_LIMITED = "invalid", "exhausted", "rate_limited"
# How long a refused key is not tried again. A wrong or expired key is not going to fix itself, so it is looked at again rarely;
# a key out of credit may be topped up; a rate limit passes in seconds (the provider's own Retry-After wins, within bounds).
COOLDOWN_SEC = {INVALID: 1800.0, EXHAUSTED: 600.0}
RATE_LIMIT_DEFAULT_SEC, RATE_LIMIT_MIN_SEC, RATE_LIMIT_MAX_SEC = 30.0, 5.0, 300.0


def split_keys(value: str) -> tuple[str, ...]:
    """The keys in a setting that holds one or several, separated by commas (spaces, semicolons and new lines also work), in
    order, without repeats. A key that cannot be sent in a request header is refused without being echoed."""
    keys = tuple(dict.fromkeys(part for part in _KEY_SEPARATORS.split(value.strip()) if part))
    if len(keys) > MAX_KEYS:
        raise ValueError(f"at most {MAX_KEYS} keys are supported")
    if any(not _KEY_SHAPE.match(key) for key in keys):
        raise ValueError("a key is empty, too short or has characters that are not allowed in a key")
    return keys


class KeyFailure(UnbilledSearchFailure):
    """A refusal that is about the key used: `kind` says whether it is wrong or expired, out of credit, or rate limited.
    The provider refuses before doing any work, so nothing is charged and another key may be tried at no cost."""

    def __init__(self, code: str, kind: str, retry_after: float | None = None) -> None:
        super().__init__(code)
        self.kind, self.retry_after = kind, retry_after


def retry_after_seconds(value: str | None) -> float | None:
    """A `Retry-After` header in seconds (the date form is not used by these providers and reads as absent)."""
    try:
        seconds = float(value) if value is not None else None
    except ValueError:
        return None
    return seconds if seconds is not None and seconds >= 0 else None


class KeyPool:
    """Which of a provider's keys can be used right now. One pool is shared by every search in the process that uses the same
    keys (`pool_for`), so what one search learns about a key is known to the next. Keys are only ever named by position."""

    def __init__(self, provider: str, keys: tuple[str, ...], clock: Callable[[], float] = time.monotonic) -> None:
        self.provider, self.keys, self._clock = provider, keys, clock
        self._until = [0.0] * len(keys)
        self._kind: list[str | None] = [None] * len(keys)

    def usable(self) -> list[int]:
        """Positions of the keys that may be tried now, in the order they were given."""
        now = self._clock()
        return [index for index, until in enumerate(self._until) if until <= now]

    def failed(self, index: int, failure: KeyFailure) -> None:
        if failure.kind == RATE_LIMITED:
            wait = min(RATE_LIMIT_MAX_SEC, max(RATE_LIMIT_MIN_SEC, failure.retry_after if failure.retry_after is not None else RATE_LIMIT_DEFAULT_SEC))
        else:
            wait = COOLDOWN_SEC[failure.kind]
        self._until[index], self._kind[index] = self._clock() + wait, failure.kind
        remaining = len(self.usable())
        logger.warning("web search key refused: provider=%s key=%d/%d reason=%s; not used for %ds; %d of %d keys usable",
                       self.provider, index + 1, len(self.keys), failure.kind, wait, remaining, len(self.keys))

    def worked(self, index: int) -> None:
        if self._kind[index] is not None:
            logger.info("web search key usable again: provider=%s key=%d/%d", self.provider, index + 1, len(self.keys))
        self._until[index], self._kind[index] = 0.0, None


_pools: dict[tuple[str, tuple[str, ...]], KeyPool] = {}
_pools_lock = threading.Lock()


def pool_for(provider: str, keys: tuple[str, ...]) -> KeyPool:
    """The shared pool for these keys. Keys are identified by a digest, so changing the keys starts a fresh pool."""
    identity = (provider, tuple(hashlib.sha256(key.encode()).hexdigest() for key in keys))
    with _pools_lock:
        if identity not in _pools:
            _pools[identity] = KeyPool(provider, keys)
        return _pools[identity]


class PooledClient:
    """A provider's client over all of its keys. It has the interface of a single client (`search`, `max_cost_usd`,
    `timeout_sec`), so the tool does not care how many keys stand behind a provider."""

    def __init__(self, pool: KeyPool, build: Callable[[str], "SearchClient"], *, max_cost_usd, timeout_sec: float) -> None:
        self.pool, self.build, self.max_cost_usd, self.timeout_sec = pool, build, max_cost_usd, timeout_sec

    async def search(self, query: str, max_results: int, *, allowed_hosts: tuple[str, ...] = ()) -> SearchResponse:
        order = self.pool.usable()
        if not order:
            raise UnbilledSearchFailure("SEARCH_KEYS_UNAVAILABLE")
        last: KeyFailure | None = None
        # The provider's whole share of the search, however many keys it takes: a slow first key leaves the next less time,
        # never more than the tool allows.
        async with asyncio.timeout(self.timeout_sec):
            for index in order:
                try:
                    response = await self.build(self.pool.keys[index]).search(query, max_results, allowed_hosts=allowed_hosts)
                except KeyFailure as failure:
                    self.pool.failed(index, failure)
                    last = failure
                    continue
                self.pool.worked(index)
                return response
        raise UnbilledSearchFailure(last.args[0] if last else "SEARCH_KEYS_UNAVAILABLE")


class SearchClient:
    """What `PooledClient` needs of one provider's client (a type for readers; `ExaClient` and `TavilyClient` satisfy it)."""

    async def search(self, query: str, max_results: int, *, allowed_hosts: tuple[str, ...] = ()) -> SearchResponse:  # pragma: no cover
        raise NotImplementedError
