import asyncio
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.scheduler import daily_report_loop, next_run_at, run_report_job

ROME = ZoneInfo("Europe/Rome")
AT = time(7, 30)


def test_next_run_is_today_when_time_not_reached():
    now = datetime(2026, 11, 16, 5, 0, tzinfo=UTC)  # 06:00 in Rome
    assert next_run_at(now, AT, ROME) == datetime(2026, 11, 16, 7, 30, tzinfo=ROME)


def test_next_run_is_tomorrow_when_time_passed_or_exact():
    after = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)  # 09:00 in Rome
    exact = datetime(2026, 11, 16, 6, 30, tzinfo=UTC)  # 07:30 in Rome
    expected = datetime(2026, 11, 17, 7, 30, tzinfo=ROME)
    assert next_run_at(after, AT, ROME) == expected
    assert next_run_at(exact, AT, ROME) == expected


async def test_job_sends_the_report():
    sent: list[str] = []

    async def produce() -> str:
        return "tutto ok"

    async def send(text: str) -> None:
        sent.append(text)

    await run_report_job(produce, send)
    assert sent == ["tutto ok"]


async def test_failing_job_still_sends_an_error_message():
    sent: list[str] = []

    async def produce() -> str:
        raise RuntimeError("boom")

    async def send(text: str) -> None:
        sent.append(text)

    await run_report_job(produce, send)
    assert len(sent) == 1 and "fallito" in sent[0] and "boom" in sent[0]


async def test_failing_delivery_does_not_raise():
    async def produce() -> str:
        return "x"

    async def send(text: str) -> None:
        raise OSError("telegram down")

    await run_report_job(produce, send)


async def test_loop_waits_for_the_time_then_fires_once_per_day():
    clock = {"t": datetime(2026, 11, 16, 5, 0, tzinfo=UTC)}  # 06:00 in Rome
    fired: list[datetime] = []

    async def sleep(seconds: float) -> None:
        clock["t"] = clock["t"].fromtimestamp(clock["t"].timestamp() + seconds, UTC)

    async def produce() -> str:
        return "report"

    async def send(text: str) -> None:
        fired.append(clock["t"])
        if len(fired) == 2:
            raise asyncio.CancelledError  # stop the endless loop after two days

    with pytest.raises(asyncio.CancelledError):
        await daily_report_loop(produce, send, AT, ROME, now=lambda: clock["t"], sleep=sleep)
    assert [t.astimezone(ROME).strftime("%d %H:%M") for t in fired] == ["16 07:30", "17 07:30"]
