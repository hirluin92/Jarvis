"""The weekly review (phase 11): for each product the numbers, the work done, the decisions to look
at again and three proposed moves; a button turns a move into a draft spec of phase 10."""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.agent.loop import AgentContext, run_agent
from app.catalog import get_catalog
from app.db import session as db
from app.db.models import ReviewMove, Spec

log = logging.getLogger("jarvis.review")
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MOVE_RE = re.compile(r"^\s*([1-3])[.)]\s*(.+?)\s+[—–-]\s+(.+?)\s*$", re.M)

REVIEW_PROMPT = """Weekly review of the product {product}. Use the tools and the documents: \
get_product_brief (numbers and their age), site_status and github_activity (what is running and \
what changed), build_status (work done), list_decisions with due_only=true (decisions to look at \
again), list_metrics (the numbers of the week, each with its source), search_docs when a \
document may matter. Write in Italian, at most 20 lines, in this order: Numeri (each with \
its source, also from list_metrics; say what is missing), Lavoro fatto, Decisioni da \
riesaminare. Then propose exactly THREE moves for the coming week, each with the reason, \
based on those facts. Never invent a number. The very last part must be the line "MOSSE:" \
followed by three lines in this exact form:
1. <short title> — <reason>
2. <short title> — <reason>
3. <short title> — <reason>"""


def next_weekly(now: datetime, day: str, at: time, tz: ZoneInfo) -> datetime:
    """The next time that falls on weekday `day` at local time `at`, strictly after `now`."""
    target_wd = DAYS.index(day.lower()[:3])
    local = now.astimezone(tz)
    candidate = datetime.combine(local.date(), at, tzinfo=tz)
    candidate += timedelta(days=(target_wd - local.weekday()) % 7)
    if candidate <= local:
        candidate += timedelta(days=7)
    return candidate


def parse_moves(answer: str) -> tuple[str, list[tuple[str, str]]]:
    """(the review text without the MOSSE block, up to three (title, reason))."""
    head, _, tail = answer.partition("MOSSE:")
    moves = [(m.group(2).strip(), m.group(3).strip()) for m in MOVE_RE.finditer(tail)]
    return head.strip(), moves[:3]


async def store_moves(product: str, moves: list[tuple[str, str]]) -> list[ReviewMove]:
    async with db.session_maker()() as s:
        rows = [ReviewMove(product=product, title=t[:300], reason=r) for t, r in moves]
        s.add_all(rows)
        await s.commit()
        ids = [r.id for r in rows]
    async with db.session_maker()() as s:
        return [await s.get(ReviewMove, i) for i in ids]  # type: ignore[misc]


async def review_product(
    product: str,
    make_ctx: Callable[[str], AgentContext],
    send: Callable[[str, list[ReviewMove]], Awaitable[None]],
) -> None:
    result = await run_agent(REVIEW_PROMPT.format(product=product), make_ctx(product))
    if result.status != "completed":
        raise RuntimeError(f"review of {product} ended with status {result.status}")
    text, moves = parse_moves(result.answer)
    rows = await store_moves(product, moves) if moves else []
    body = f"📋 Riesame settimanale · {product}\n\n{text}"
    if rows:
        body += "\n\nMosse proposte:\n" + "\n".join(
            f"{i}. {m.title} — {m.reason}" for i, m in enumerate(rows, 1)
        )
    await send(body[:3900], rows)


async def run_review(
    make_ctx: Callable[[str], AgentContext],
    send: Callable[[str, list[ReviewMove]], Awaitable[None]],
) -> int:
    """Review every product of the catalog; a failing one is reported, the others go on."""
    done = 0
    for product in get_catalog().products:
        try:
            await review_product(product.name, make_ctx, send)
            done += 1
        except Exception as e:
            log.exception("review of %s failed", product.name)
            await send(f"Riesame di {product.name} fallito: {type(e).__name__}: {e}", [])
    return done


async def weekly_review_loop(
    run: Callable[[], Awaitable[object]],
    day: str,
    at: time,
    tz: ZoneInfo,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    while True:
        target = next_weekly(now(), day, at, tz)
        while (remaining := (target - now()).total_seconds()) > 0:
            await sleep(min(remaining, 60))  # short sleeps: a suspended PC fires late, not never
        try:
            await run()
        except Exception:
            log.exception("weekly review failed")


async def move_to_spec(move_id: int, make_ctx: Callable[[str], AgentContext]) -> str:
    """Turn a proposed move into a draft spec (phase 10). Returns what to tell the user."""
    async with db.session_maker()() as s:
        move = await s.get(ReviewMove, move_id)
    if move is None:
        return "Mossa non trovata."
    if move.spec_id:
        return f"Per questa mossa esiste già la bozza di specifica #{move.spec_id}."
    prompt = (
        f"Write with draft_spec the specification for this move on {move.product}: "
        f"{move.title}. Reason: {move.reason}. Then show it. If the product does not accept "
        "builds, say so and do not insist."
    )
    result = await run_agent(prompt, make_ctx(move.product))
    async with db.session_maker()() as s:
        spec = (
            (
                await s.execute(
                    select(Spec)
                    .where(Spec.product == move.product, Spec.created_at >= move.created_at)
                    .order_by(Spec.id.desc())
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        if spec is not None:
            row = await s.get(ReviewMove, move_id)
            row.spec_id = spec.id
            await s.commit()
    tail = (
        f"\n\nBozza di specifica #{spec.id}. Per avviare il lavoro dimmi «avvia il lavoro per la "
        f"specifica {spec.id}»: vedrai la specifica intera e confermerai qui."
        if spec
        else ""
    )
    return (result.answer or "(nessuna risposta)") + tail
