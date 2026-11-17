from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import advisor, builds, knowledge, review
from app.agent.llm import FakeLLMClient, LLMResponse, ToolUse
from app.agent.loop import AgentContext, resume_confirmed, run_agent
from app.catalog import Catalog, Product
from app.db import session as db
from app.db.models import AdvisorSession, Decision, ReviewMove, Spec
from app.interfaces.telegram import BotState, switch_advisor
from app.tools.builtin import (
    ADVISOR_ONLY,
    default_registry,
    everyday_registry,
    get_product_brief,
    list_decisions,
    log_decision,
    save_session_summary,
    search_docs,
    update_product_brief,
)
from app.voice.realtime import TurnClock, VoiceTurnHandler

ROME = ZoneInfo("Europe/Rome")


@pytest.fixture
def catalog(monkeypatch):
    cat = Catalog(
        products=[
            Product(name="QuadroRW", github_repo="me/q", local_path=".", build_enabled=True),
            Product(name="IMS"),
        ]
    )
    for module in (advisor, builds, review, search_docs):
        if hasattr(module, "get_catalog"):
            monkeypatch.setattr(module, "get_catalog", lambda: cat)
    return cat


PLAN = """# Piano commerciale

## Prezzi
Il piano base costa 49 euro al mese. Il piano studio costa 149 euro al mese.

## Canali
Si parte dai commercialisti con il passaparola.

# Ricerca
## Concorrenti
Il concorrente principale costa 99 euro al mese.
"""


# ---------- documents ----------


def test_markdown_is_split_by_headings_with_their_path():
    sections = dict(knowledge.split_markdown(PLAN))
    assert (
        "Piano commerciale > Prezzi" in sections
        and "49 euro" in sections["Piano commerciale > Prezzi"]
    )
    assert "Ricerca > Concorrenti" in sections
    long = "# T\n\n" + "\n\n".join("paragrafo " * 60 for _ in range(8))
    assert all(len(t) <= knowledge.MAX_CHUNK_CHARS for _, t in knowledge.split_markdown(long))


async def test_documents_are_imported_replaced_and_found_with_their_source(
    tracer, catalog, tmp_path
):
    (tmp_path / "QuadroRW").mkdir()
    (tmp_path / "QuadroRW" / "piano.md").write_text(PLAN, encoding="utf-8")
    (tmp_path / "IMS").mkdir()
    (tmp_path / "IMS" / "note.md").write_text("# Note\nIl prezzo non c'entra.\n", encoding="utf-8")
    assert await knowledge.import_dir(tmp_path) == {"IMS": 1, "QuadroRW": 3}
    assert await knowledge.import_dir(tmp_path) == {"IMS": 1, "QuadroRW": 3}  # re-import replaces

    out = await search_docs.search_docs(
        search_docs.DocsInput(product="quadrorw", query="quanto costa il piano base?")
    )
    assert out["found"] >= 1 and out["passages"][0]["source"] == "piano.md"
    assert "49 euro" in out["passages"][0]["text"] and "untrusted" not in out["note"]  # data note
    assert (
        await search_docs.search_docs(search_docs.DocsInput(product="IMS", query="piano base"))
    )["found"] == 0  # other products' documents are not searched
    with pytest.raises(ValueError, match="Unknown product"):
        await search_docs.search_docs(search_docs.DocsInput(product="Boh", query="qualcosa qui"))


# ---------- brief and decisions ----------


async def test_brief_is_updated_field_by_field_and_dated(tracer, catalog):
    empty = await get_product_brief.get_product_brief(
        get_product_brief.BriefInput(product="QuadroRW")
    )
    assert empty["exists"] is False
    await update_product_brief.update_product_brief(
        update_product_brief.UpdateBriefInput(
            product="quadrorw", key_numbers="12 clienti, 300 euro/mese"
        )
    )
    out = await update_product_brief.update_product_brief(
        update_product_brief.UpdateBriefInput(product="QuadroRW", risks="dipendenza da un canale")
    )
    assert (
        out["key_numbers"] == "12 clienti, 300 euro/mese"
        and out["risks"] == "dipendenza da un canale"
    )
    assert out["age_days"] == 0 and out["updated_at"] == datetime.now(UTC).date().isoformat()
    with pytest.raises(ValueError, match="nessun campo"):
        await advisor.update_brief("QuadroRW")


async def test_a_new_decision_that_reverses_an_old_one_is_flagged(tracer, catalog):
    first = await log_decision.log_decision(
        log_decision.DecisionInput(
            product="QuadroRW",
            decision="Non attiviamo Stripe prima di 50 clienti paganti",
            reasons="i costi fissi di Stripe sono alti per pochi clienti",
            review_on=date(2026, 12, 1),
        )
    )
    assert first["possibly_related_earlier_decisions"] == []
    second = await log_decision.log_decision(
        log_decision.DecisionInput(
            product="QuadroRW",
            decision="Attiviamo Stripe subito per i clienti paganti",
            contradicts=[first["id"]],
        )
    )
    assert [d["id"] for d in second["possibly_related_earlier_decisions"]] == [first["id"]]
    assert "contraddice" in second["warning"]
    with pytest.raises(ValueError, match="inesistenti"):
        await advisor.log_decision("QuadroRW", "Una decisione nuova", contradicts=[999])


async def test_a_decision_is_recalled_in_a_new_session_and_due_ones_are_listed(tracer, catalog):
    await advisor.log_decision(
        "QuadroRW", "Prezzo base a 49 euro al mese", review_on=date(2026, 1, 1)
    )
    await advisor.log_decision(
        "QuadroRW", "Si parte dai commercialisti", review_on=date(2099, 1, 1)
    )
    fresh = FakeLLMClient(
        [
            LLMResponse(tool_uses=[ToolUse("1", "list_decisions", {"product": "QuadroRW"})]),
            LLMResponse(text="Avevamo deciso il prezzo base a 49 euro."),
        ]
    )
    ctx = AgentContext(llm=fresh, registry=default_registry(), tracer=tracer)  # a brand new session
    result = await run_agent("che avevamo deciso?", ctx)
    assert result.status == "completed"
    seen = fresh.calls[1][-1]["content"][0]["content"]
    assert "Prezzo base a 49 euro" in seen
    due = await list_decisions.list_decisions(list_decisions.ListDecisionsInput(due_only=True))
    assert [d["decision"] for d in due["decisions"]] == ["Prezzo base a 49 euro al mese"]


# ---------- the end-of-session summary needs the yes ----------


async def test_a_session_summary_is_saved_only_after_the_confirmation(tracer, catalog):
    use = ToolUse(
        "1", "save_session_summary",
        {"product": "QuadroRW", "summary": "Abbiamo discusso il prezzo e il canale. " * 2,
         "decisions": [{"decision": "Prezzo base a 49 euro", "review_on": "2026-12-15"}]},
    )  # fmt: skip
    ctx = AgentContext(
        llm=FakeLLMClient([LLMResponse(tool_uses=[use]), LLMResponse(text="Salvato.")]),
        registry=default_registry(),
        tracer=tracer,
    )
    result = await run_agent("chiudiamo la sessione", ctx)

    async def stored():
        async with db.session_maker()() as s:
            return (
                list((await s.execute(select(AdvisorSession))).scalars()),
                list((await s.execute(select(Decision))).scalars()),
            )

    assert result.status == "awaiting_confirmation" and await stored() == ([], [])
    assert "Prezzo base a 49 euro" in result.answer and "riesame 2026-12-15" in result.answer
    await resume_confirmed(ctx, result)
    sessions, decisions = await stored()
    assert len(sessions) == 1 and [d.decision for d in decisions] == ["Prezzo base a 49 euro"]


def test_advisor_tools_are_left_out_of_everyday_chats():
    everyday = {d["name"] for d in everyday_registry().definitions()}
    full = {d["name"] for d in default_registry().definitions()}
    assert not (ADVISOR_ONLY & everyday) and ADVISOR_ONLY <= full
    assert {"set_advisor_mode", "send_to_telegram", "draft_spec"} <= everyday
    assert save_session_summary.TOOL.permission == "confirm"


# ---------- the budget ----------


class Priced(FakeLLMClient):
    model = "claude-sonnet-5-5"


def reply(text="ok"):
    return LLMResponse(
        text=text, input_tokens=1_000_000, output_tokens=0, model="claude-sonnet-5-5"
    )


async def test_the_advisor_stops_at_the_daily_budget_and_tells_once(tracer):
    inner = Priced([reply(), reply()])  # each call costs 2 dollars
    told: list[str] = []
    day = {"d": date(2026, 11, 17)}

    async def tell(text: str) -> None:
        told.append(text)

    llm = advisor.BudgetedLLM(inner, 3.0, tell, today=lambda: day["d"])
    assert (await llm.complete("s", [], [])).text == "ok"  # 2.00 spent
    assert (await llm.complete("s", [], [])).text == "ok"  # 4.00 spent: over the cap now
    blocked = await llm.complete("s", [], [])
    assert "tetto di spesa" in blocked.text and len(inner.calls) == 2  # the model was not called
    await llm.complete("s", [], [])
    assert len(told) == 1  # told once per day
    day["d"] = date(2026, 11, 18)  # a new day starts from zero
    inner._responses.append(reply("di nuovo"))
    assert (await llm.complete("s", [], [])).text == "di nuovo"
    assert await advisor.spent_on(date(2026, 11, 17)) == pytest.approx(4.0)


async def test_without_a_budget_nothing_is_blocked(tracer):
    inner = Priced([reply(), reply()])
    llm = advisor.BudgetedLLM(inner, 0)
    await llm.complete("s", [], [])
    await llm.complete("s", [], [])
    assert len(inner.calls) == 2


def test_the_advisor_model_is_chosen_from_the_environment():
    base = dict(
        llm_provider="groq", advisor_provider="", advisor_model="", anthropic_api_key="k",
        anthropic_model="claude-sonnet-5-5", gemini_api_key="g", gemini_model="gemini-x",
        groq_api_key="q", groq_model="openai/gpt-oss-120b",
    )  # fmt: skip
    assert advisor.build_advisor_llm(SimpleNamespace(**base)).model == "openai/gpt-oss-120b"
    big = SimpleNamespace(
        **{**base, "advisor_provider": "anthropic", "advisor_model": "claude-opus-5-5"}
    )
    assert advisor.build_advisor_llm(big).model == "claude-opus-5-5"
    with pytest.raises(ValueError, match="ADVISOR_PROVIDER"):
        advisor.build_advisor_llm(SimpleNamespace(**{**base, "advisor_provider": "boh"}))


# ---------- weekly review ----------


def test_the_review_day_and_time_are_respected():
    mon_late = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)  # Monday 13:00 Rome
    assert review.next_weekly(mon_late, "mon", time(8, 0), ROME) == datetime(
        2026, 11, 23, 8, 0, tzinfo=ROME
    )
    wed = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
    assert review.next_weekly(wed, "fri", time(8, 0), ROME) == datetime(
        2026, 11, 20, 8, 0, tzinfo=ROME
    )
    assert review.next_weekly(
        datetime(2026, 11, 16, 6, 0, tzinfo=UTC), "mon", time(8, 0), ROME
    ) == (datetime(2026, 11, 16, 8, 0, tzinfo=ROME))


ANSWER = """Numeri: 12 clienti (scheda).
Lavoro fatto: nulla.
MOSSE:
1. Chiamare 5 commercialisti — la scheda dice che il canale è uno solo
2. Aggiungere l'esportazione CSV — richiesta da tre clienti
3. Rivedere il prezzo — decisione da riesaminare"""


def test_the_three_moves_are_parsed():
    text, moves = review.parse_moves(ANSWER)
    assert text.startswith("Numeri") and "MOSSE" not in text
    assert [m[0] for m in moves] == [
        "Chiamare 5 commercialisti", "Aggiungere l'esportazione CSV", "Rivedere il prezzo",
    ]  # fmt: skip
    assert moves[0][1].startswith("la scheda")


async def test_the_weekly_review_arrives_and_a_move_becomes_a_draft_spec(tracer, catalog):
    sent: list[tuple[str, list]] = []

    async def send(text, moves):
        sent.append((text, moves))

    def make_ctx(product):
        use = ToolUse(
            "1", "draft_spec",
            {"product": "QuadroRW", "title": "Esportazione CSV",
             "text": "Obiettivo: esportare in CSV.\nIncluso: pulsante.\nEscluso: tutto il resto."},
        )  # fmt: skip
        script = (
            [LLMResponse(text=ANSWER)]
            if product == "QuadroRW" and not sent
            else [LLMResponse(tool_uses=[use]), LLMResponse(text="Ecco la bozza.")]
        )
        return AgentContext(llm=FakeLLMClient(script), registry=default_registry(), tracer=tracer)

    catalog.products = [catalog.products[0]]
    assert await review.run_review(make_ctx, send) == 1
    text, moves = sent[0]
    assert (
        "Riesame settimanale · QuadroRW" in text
        and len(moves) == 3
        and "Aggiungere l'esportazione CSV" in text
    )
    async with db.session_maker()() as s:
        assert len(list((await s.execute(select(ReviewMove))).scalars())) == 3

    answer = await review.move_to_spec(moves[1].id, make_ctx)  # the button of move 2
    assert "Bozza di specifica #" in answer
    async with db.session_maker()() as s:
        spec = (await s.execute(select(Spec))).scalar_one()
        assert (
            spec.title == "Esportazione CSV" and spec.status == "draft"
        )  # a draft, nothing queued
        assert (await s.get(ReviewMove, moves[1].id)).spec_id == spec.id
    assert "già la bozza" in await review.move_to_spec(moves[1].id, make_ctx)
    assert await builds.list_jobs() == []


async def test_a_failing_product_does_not_stop_the_review_of_the_others(tracer, catalog):
    sent: list[str] = []

    async def send(text, moves):
        sent.append(text)

    def make_ctx(product):
        if product == "IMS":
            raise RuntimeError("boom")
        return AgentContext(
            llm=FakeLLMClient([LLMResponse(text="Tutto fermo.")]),
            registry=default_registry(),
            tracer=tracer,
        )

    assert await review.run_review(make_ctx, send) == 1
    assert any("IMS fallito" in t for t in sent) and any("QuadroRW" in t for t in sent)


async def test_the_weekly_loop_fires_at_its_time():
    clock = {"t": datetime(2026, 11, 16, 5, 0, tzinfo=UTC)}  # Monday 06:00 Rome
    runs: list[int] = []

    async def run() -> None:
        runs.append(1)
        raise asyncio.CancelledError  # stop the loop after the first run

    async def sleep(seconds: float) -> None:
        clock["t"] += timedelta(seconds=seconds)

    import asyncio

    with pytest.raises(asyncio.CancelledError):
        await review.weekly_review_loop(
            run, "mon", time(8, 0), ROME, now=lambda: clock["t"], sleep=sleep
        )
    assert runs == [1] and clock["t"] >= datetime(
        2026, 11, 16, 7, 0, tzinfo=UTC
    )  # not before 08:00 Rome


# ---------- advisor mode ----------


async def test_advisor_mode_switches_the_conversation_and_its_model(tracer, catalog):
    everyday = FakeLLMClient([])
    advisor_llm = FakeLLMClient([])

    def make_context():
        return AgentContext(llm=everyday, registry=everyday_registry(), tracer=tracer)

    def make_advisor(product):
        ctx = AgentContext(llm=advisor_llm, registry=default_registry(), tracer=tracer)
        ctx.style, ctx.advisor = f"ADVISOR {product}", True
        return ctx

    state = BotState({1}, make_context, tracer, make_advisor_context=make_advisor)
    assert (await state.ctx(1)).llm is everyday
    assert "QuadroRW" in await switch_advisor(state, 1, "quadrorw")
    ctx = await state.ctx(1)
    assert (
        ctx.llm is advisor_llm
        and ctx.advisor
        and "search_docs" in {d["name"] for d in ctx.registry.definitions()}
    )
    assert "non è" in (await switch_advisor(state, 1, "Inesistente")).lower() or "Unknown" in (
        await switch_advisor(state, 1, "Inesistente")
    )
    assert state.advisor[1] == "QuadroRW"  # a wrong name does not change the mode
    assert "chiusa" in await switch_advisor(state, 1, None)
    assert (await state.ctx(1)).llm is everyday


async def test_an_advisor_conversation_keeps_its_model_by_voice(tracer):
    everyday = FakeLLMClient([])
    advisor_llm = FakeLLMClient([LLMResponse(text="Parere del consigliere. Fine.")])
    ctx = AgentContext(llm=advisor_llm, registry=default_registry(), tracer=tracer)
    ctx.advisor = True

    async def get_ctx():
        return ctx

    handler = VoiceTurnHandler(get_ctx, everyday, AsyncMock())
    said: list[str] = []

    async def say(sentence):
        said.append(sentence)

    await handler.respond("che ne pensi?", TurnClock(), say)
    assert len(advisor_llm.calls) == 1 and everyday.calls == []  # not swapped for the router
    assert said
