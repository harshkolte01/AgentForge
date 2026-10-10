"""Explicit framework schema setup; never called by API/worker startup."""
import asyncio
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from agentforge.settings import get_settings


async def setup():
    settings = get_settings()
    async with AsyncPostgresSaver.from_conn_string(settings.langgraph_checkpoint_db_url) as saver:
        await saver.setup()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Explicitly create/update LangGraph Postgres checkpoint tables.")
    parser.add_argument("command", choices=["setup"])
    parser.parse_args()
    try:
        asyncio.run(setup())
    except Exception:
        raise SystemExit("Checkpoint setup failed. Check the local database configuration; connection details are suppressed.") from None
    print("LangGraph checkpoint setup completed.")


if __name__ == "__main__":
    main()
