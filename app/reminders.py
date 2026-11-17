"""Reminders: stored in Postgres, delivered on Telegram by a small loop."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db import session as db
from app.db.models import Reminder

log = logging.getLogger("jarvis.reminders")
MAX_AHEAD = timedelta(days=366)
POLL_SECONDS = 15


def parse_when(when: str | None, in_minutes: int | None, tz: ZoneInfo, now: datetime) -> datetime:
    """The due time as an aware datetime. Exactly one of `when` (ISO 8601, local time if it has
    no offset) and `in_minutes` must be given; the time must be in the future, within a year."""
    if (when is None) == (in_minutes is None):
        raise ValueError("give exactly one of 'when' and 'in_minutes'")
    if in_minutes is not None:
        due = now + timedelta(minutes=in_minutes)
    else:
        try:
            parsed = datetime.fromisoformat(str(when))
        except ValueError as e:
            raise ValueError(f"'when' is not an ISO 8601 date and time: {when!r}") from e
        due = parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)
    if due <= now:
        raise ValueError("the reminder time is in the past")
    if due - now > MAX_AHEAD:
        raise ValueError("the reminder time is more than a year away")
    return due.astimezone(UTC)


async def create(text: str, due_at: datetime) -> Reminder:
    async with db.session_maker()() as s:
        r = Reminder(text=text, due_at=due_at)
        s.add(r)
        await s.commit()
        rid = r.id
    async with db.session_maker()() as s:  # read back: the proof is what is really stored
        saved = await s.get(Reminder, rid)
    if saved is None:
        raise RuntimeError(f"reminder {rid} not found after saving")
    return saved


async def pending() -> list[Reminder]:
    async with db.session_maker()() as s:
        q = select(Reminder).where(Reminder.status == "pending").order_by(Reminder.due_at)
        return list((await s.execute(q)).scalars())


async def cancel(reminder_id: int) -> Reminder | None:
    """Cancel a pending reminder. Returns it, or None if there is no pending one with that id."""
    async with db.session_maker()() as s:
        r = await s.get(Reminder, reminder_id)
        if r is None or r.status != "pending":
            return None
        r.status = "cancelled"
        await s.commit()
    async with db.session_maker()() as s:
        return await s.get(Reminder, reminder_id)


async def deliver_due(send: Callable[[str], Awaitable[None]], now: datetime | None = None) -> int:
    """Send every reminder whose time has come; returns how many were delivered. A reminder is
    marked sent only after `send` worked, so a failed delivery is retried on the next round."""
    now = now or datetime.now(UTC)
    async with db.session_maker()() as s:
        q = select(Reminder).where(Reminder.status == "pending", Reminder.due_at <= now)
        due = list((await s.execute(q.order_by(Reminder.due_at))).scalars())
    sent = 0
    for r in due:
        try:
            await send(f"⏰ Promemoria: {r.text}")
        except Exception:
            log.exception("reminder %s delivery failed, will retry", r.id)
            continue
        async with db.session_maker()() as s:
            row = await s.get(Reminder, r.id)
            if row is not None and row.status == "pending":
                row.status, row.sent_at = "sent", datetime.now(UTC)
                await s.commit()
        sent += 1
    return sent


async def reminder_loop(send: Callable[[str], Awaitable[None]]) -> None:
    while True:
        try:
            await deliver_due(send)
        except Exception:
            log.exception("reminder round failed")
        await asyncio.sleep(POLL_SECONDS)
