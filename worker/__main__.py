"""The build worker: `uv run python -m worker`. Runs on the PC, outside Jarvis' container."""

import asyncio
import logging

from app.config import get_settings
from app.db import session as db
from worker.runner import run_forever


async def main() -> None:
    settings = get_settings()
    db.init_db(settings.database_url)
    logging.info("build worker started (max parallel: %s)", settings.build_max_parallel)
    await run_forever(settings)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    asyncio.run(main())
