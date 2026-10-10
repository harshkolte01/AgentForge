"""Windows-compatible asyncio runner for psycopg async connections."""

import asyncio
import sys


def run_async(coro):
    """Run a coroutine. psycopg cannot use Windows ProactorEventLoop."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(coro)
