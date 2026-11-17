"""The advisor (phase 11): product briefs, decisions, session summaries, spend cap, own model."""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select

from app.agent.llm import (
    AnthropicClient,
    GeminiClient,
    LLMClient,
    LLMResponse,
    OpenAICompatClient,
    TextSink,
)
from app.catalog import get_catalog
from app.costs import estimate_cost
from app.db import session as db
from app.db.models import AdvisorSession, AdvisorSpend, Decision, ProductBrief
from app.memory import _stems

log = logging.getLogger("jarvis.advisor")
BRIEF_FIELDS = ("objective", "customer", "status", "key_numbers", "risks", "next_moves")
RELATED_MIN_SHARED_WORDS = 2


def canonical(product: str) -> str:
    """The catalog name of a product (case-insensitive); unknown products are an error.
    "Nicchie" is the virtual workspace of the niche radar: it is not a product."""
    if product.strip().lower() == "nicchie":
        return "Nicchie"
    if product.strip().lower() == "clienti":
        return "Clienti"
    (found,) = get_catalog().select(product)
    return found.name


# ---------- product briefs ----------


async def get_brief(product: str) -> ProductBrief | None:
    async with db.session_maker()() as s:
        return await s.get(ProductBrief, canonical(product))


async def update_brief(product: str, **fields: str | None) -> ProductBrief:
    name = canonical(product)
    changes = {k: v for k, v in fields.items() if k in BRIEF_FIELDS and v is not None}
    if not changes:
        raise ValueError("nessun campo da aggiornare")
    async with db.session_maker()() as s:
        row = await s.get(ProductBrief, name)
        if row is None:
            row = ProductBrief(product=name)
            s.add(row)
        for k, v in changes.items():
            setattr(row, k, v)
        row.updated_at = datetime.now(UTC)
        await s.commit()
    saved = await get_brief(name)
    assert saved is not None  # read back: the proof is what is really stored
    return saved


def brief_dict(b: ProductBrief) -> dict[str, Any]:
    updated = b.updated_at if b.updated_at.tzinfo else b.updated_at.replace(tzinfo=UTC)
    return {
        "product": b.product,
        **{f: getattr(b, f) for f in BRIEF_FIELDS},
        "updated_at": updated.date().isoformat(),
        "age_days": (datetime.now(UTC) - updated).days,
    }


# ---------- decisions ----------


async def log_decision(
    product: str,
    decision: str,
    reasons: str = "",
    alternatives: str = "",
    review_on: date | None = None,
    contradicts: list[int] | None = None,
) -> tuple[Decision, list[Decision]]:
    """Save a decision. Returns it and the earlier decisions on the same product that share
    enough words with it to be worth a look (a possible contradiction, for the user to judge)."""
    name = canonical(product)
    earlier = await list_decisions(name)
    known = {d.id for d in earlier}
    bad = [i for i in (contradicts or []) if i not in known]
    if bad:
        raise ValueError(f"decisioni inesistenti per {name}: {bad}")
    mine = _stems(decision + " " + reasons)
    related = [
        d
        for d in earlier
        if len(mine & _stems(d.decision + " " + d.reasons)) >= RELATED_MIN_SHARED_WORDS
    ]
    async with db.session_maker()() as s:
        row = Decision(
            product=name, decision=decision, reasons=reasons, alternatives=alternatives,
            review_on=review_on, contradicts=list(contradicts or []),
        )  # fmt: skip
        s.add(row)
        await s.commit()
        rid = row.id
    async with db.session_maker()() as s:
        saved = await s.get(Decision, rid)
    assert saved is not None
    return saved, related


async def list_decisions(product: str | None = None, due_by: date | None = None) -> list[Decision]:
    async with db.session_maker()() as s:
        q = select(Decision).order_by(Decision.id.desc())
        if product:
            q = q.where(Decision.product == canonical(product))
        rows = list((await s.execute(q)).scalars())
    if due_by is not None:
        rows = [d for d in rows if d.review_on is not None and d.review_on <= due_by]
    return rows


def decision_dict(d: Decision) -> dict[str, Any]:
    return {
        "id": d.id,
        "product": d.product,
        "decision": d.decision,
        "reasons": d.reasons,
        "alternatives": d.alternatives,
        "review_on": d.review_on.isoformat() if d.review_on else None,
        "contradicts": d.contradicts or [],
        "date": d.created_at.date().isoformat(),
    }


# ---------- session summaries ----------


async def save_summary(product: str, summary: str) -> AdvisorSession:
    async with db.session_maker()() as s:
        row = AdvisorSession(product=canonical(product), summary=summary)
        s.add(row)
        await s.commit()
        rid = row.id
    async with db.session_maker()() as s:
        saved = await s.get(AdvisorSession, rid)
    assert saved is not None
    return saved


# ---------- the advisor's model and its daily budget ----------


def build_advisor_llm(s: Any) -> LLMClient:
    """The advisor's own model: ADVISOR_PROVIDER / ADVISOR_MODEL, defaulting to the everyday one."""
    provider = s.advisor_provider or s.llm_provider
    if provider == "anthropic":
        return AnthropicClient(s.anthropic_api_key, s.advisor_model or s.anthropic_model)
    if provider == "gemini":
        return GeminiClient(s.gemini_api_key, s.advisor_model or s.gemini_model)
    if provider == "groq":
        return OpenAICompatClient(
            "https://api.groq.com/openai/v1", s.groq_api_key, s.advisor_model or s.groq_model
        )
    raise ValueError(f"unknown ADVISOR_PROVIDER: {provider}")


async def spent_on(day: date) -> float:
    async with db.session_maker()() as s:
        row = await s.get(AdvisorSpend, day)
        return row.usd if row else 0.0


async def add_spend(day: date, usd: float) -> None:
    if usd <= 0:
        return
    async with db.session_maker()() as s:
        row = await s.get(AdvisorSpend, day)
        if row is None:
            s.add(AdvisorSpend(day=day, usd=usd))
        else:
            row.usd += usd
        await s.commit()


class BudgetedLLM:
    """The advisor's model behind a daily spend cap. Once the cap is reached it stops calling the
    model, answers with a clear message and tells the user once a day."""

    def __init__(
        self,
        inner: LLMClient,
        daily_budget: float,
        notify: Callable[[str], Awaitable[None]] | None = None,
        today: Callable[[], date] = lambda: datetime.now(UTC).date(),
    ) -> None:
        self.inner, self.budget, self._notify, self._today = inner, daily_budget, notify, today
        self.model = inner.model
        self._told: date | None = None

    async def complete(
        self, system, messages, tools, on_text: TextSink | None = None
    ) -> LLMResponse:
        day = self._today()
        if self.budget > 0:
            spent = await spent_on(day)
            if spent >= self.budget:
                msg = (
                    f"Ho raggiunto il tetto di spesa giornaliero del consigliere "
                    f"({spent:.2f} di {self.budget:.2f} dollari): mi fermo fino a domani, "
                    "oppure alza ADVISOR_DAILY_BUDGET."
                )
                if self._notify is not None and self._told != day:
                    self._told = day
                    try:
                        await self._notify("⛔ " + msg)
                    except Exception:
                        log.exception("budget notice failed")
                if on_text is not None:
                    await on_text(msg)
                    await on_text("\n")
                return LLMResponse(text=msg, model=self.model)
        kw: dict[str, Any] = {"on_text": on_text} if on_text else {}
        resp = await self.inner.complete(system, messages, tools, **kw)
        cost = estimate_cost(resp.model or self.model, resp.input_tokens, resp.output_tokens)
        if cost is None and self.budget > 0:
            log.warning("no price known for %s: its spend is counted as 0", self.model)
        await add_spend(day, cost or 0.0)
        return resp


# ---------- what the advisor always has in front of it ----------

CONTEXT_MAX_DECISIONS = 8
CONTEXT_MAX_CHARS = 2500


async def context_for(product: str) -> str:
    """The registered decisions and the brief of a product, as data for the advisor's prompt.
    Put there by the code, so the model does not have to remember to look for them."""
    lines: list[str] = []
    if canonical(product) == "Clienti":
        from app import growth

        return await growth.overview()
    if canonical(product) == "Nicchie":
        from app import opportunities

        for op in await opportunities.list_all(limit=12):
            value, coverage, _ = opportunities.total(op)
            n = len(await opportunities.evidence_of(op.id))
            score = "n/d" if value is None else f"{value:.0f}/100 (criteri noti {coverage:.0%})"
            lines.append(f"- #{op.id} {op.title} [{op.status}] score {score}, {n} prove")
        if not lines:
            return ""
        return "\n".join(["Opportunities under study (data):", *lines])[:CONTEXT_MAX_CHARS]
    decisions = (await list_decisions(product))[:CONTEXT_MAX_DECISIONS]
    if decisions:
        lines.append(f"Registered decisions on {canonical(product)} (data, newest first):")
        for d in decisions:
            review = f", review {d.review_on.isoformat()}" if d.review_on else ""
            why = f" - reasons: {d.reasons}" if d.reasons else ""
            lines.append(f"- #{d.id} ({d.created_at.date().isoformat()}{review}) {d.decision}{why}")
    brief = await get_brief(product)
    if brief is not None:
        b = brief_dict(brief)
        lines.append(f"Product brief (data, updated {b['updated_at']}, {b['age_days']} days ago):")
        lines += [f"- {f}: {b[f]}" for f in BRIEF_FIELDS if b[f]]
    return "\n".join(lines)[:CONTEXT_MAX_CHARS]


def with_advisor_data(
    product: str, base: Callable[[str], Awaitable[str]] | None
) -> Callable[[str], Awaitable[str]]:
    """A `system_extra` for an advisor conversation: the usual extras plus decisions and brief."""

    async def extra(user_message: str) -> str:
        parts = [await base(user_message)] if base is not None else []
        try:
            parts.append(await context_for(product))
        except Exception:  # advisor data trouble must never stop an answer
            log.exception("could not load the advisor data")
        return "\n\n".join(p for p in parts if p)

    return extra
