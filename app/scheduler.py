import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger("jarvis.scheduler")

MAX_SLEEP_SECONDS = 60


def next_run_at(now: datetime, at: time, tz: ZoneInfo) -> datetime:
    """Next occurrence of local time `at` strictly after `now`."""
    local = now.astimezone(tz)
    candidate = datetime.combine(local.date(), at, tzinfo=tz)
    if candidate <= local:
        candidate = datetime.combine(local.date() + timedelta(days=1), at, tzinfo=tz)
    return candidate


async def run_report_job(
    produce: Callable[[], Awaitable[str]], send: Callable[[str], Awaitable[None]]
) -> None:
    """Produce and deliver the report; a failure still sends an error message."""
    try:
        text = await produce()
    except Exception as e:
        log.exception("morning report failed")
        text = f"Report mattutino fallito: {type(e).__name__}: {e}"
    try:
        await send(text)
    except Exception:
        log.exception("morning report delivery failed")


async def daily_report_loop(
    produce: Callable[[], Awaitable[str]],
    send: Callable[[str], Awaitable[None]],
    at: time,
    tz: ZoneInfo,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    while True:
        target = next_run_at(now(), at, tz)
        # short sleeps re-check the wall clock, so a suspended PC fires late instead of skipping
        while (remaining := (target - now()).total_seconds()) > 0:
            await sleep(min(remaining, MAX_SLEEP_SECONDS))
        await run_report_job(produce, send)
