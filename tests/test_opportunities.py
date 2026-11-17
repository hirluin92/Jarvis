from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app import advisor, builds, opportunities, radar
from app.agent.llm import FakeLLMClient, LLMResponse, ToolUse
from app.agent.loop import AgentContext, resume_confirmed, run_agent
from app.catalog import Catalog, Product
from app.db import session as db
from app.db.models import BuildJob
from app.tools.builtin import (
    ADVISOR_ONLY,
    decide_opportunity,
    default_registry,
    everyday_registry,
    score_opportunity,
)

TODAY = datetime.now(UTC).date()


@pytest.fixture
def catalog(monkeypatch):
    cat = Catalog(
        products=[Product(name="Fatture", github_repo="me/f", local_path=".", build_enabled=True)]
    )
    for module in (advisor, builds):
        monkeypatch.setattr(module, "get_catalog", lambda: cat)
    return cat


def quote(n: int) -> str:
    return f"Un commercialista scrive: perdo ore ogni mese con le fatture numero {n}."


async def seed(title="Fatture per commercialisti", n=3, **kw):
    op = await opportunities.save(title, "commercialisti", "perdono tempo con le fatture", **kw)
    ids = []
    for i in range(n):
        e = await opportunities.add_evidence(
            op.id, "complaint", f"https://forum.example/t/{i}", quote(i), TODAY
        )
        ids.append(e.id)
    return op, ids


# ---------- evidence ----------


async def test_evidence_needs_a_real_address_and_the_words_read(tracer):
    op = await opportunities.save("Idea di prova", "x", "y")
    with pytest.raises(ValueError, match="indirizzo web"):
        await opportunities.add_evidence(op.id, "complaint", "ftp://x", quote(1))
    with pytest.raises(ValueError, match="indirizzo web"):
        await opportunities.add_evidence(op.id, "complaint", "non un url", quote(1))
    with pytest.raises(ValueError, match="citazione"):
        await opportunities.add_evidence(op.id, "complaint", "https://x.it/a", "troppo corta")
    with pytest.raises(ValueError, match="tipo di prova"):
        await opportunities.add_evidence(op.id, "boh", "https://x.it/a", quote(1))
    assert (await opportunities.get(op.id)).status == "idea"
    await opportunities.add_evidence(op.id, "price", "https://x.it/a", quote(1))
    assert (await opportunities.get(op.id)).status == "scouted"
    with pytest.raises(ValueError, match="già presente"):
        await opportunities.add_evidence(op.id, "price", "https://x.it/a", quote(1))


# ---------- the score needs evidence ----------


async def test_a_number_without_evidence_is_refused_and_unknown_is_not_zero(tracer):
    op, ids = await seed(n=3)
    other, other_ids = await seed("Un'altra idea", n=1)
    with pytest.raises(ValueError, match="senza prove"):
        await opportunities.score(op.id, {"size": {"value": 5, "why": "enorme"}})
    with pytest.raises(ValueError, match="senza prove"):  # evidence of ANOTHER opportunity
        await opportunities.score(
            op.id, {"size": {"value": 5, "why": "x", "evidence_ids": other_ids}}
        )
    with pytest.raises(ValueError, match="da 0 a 5"):
        await opportunities.score(op.id, {"size": {"value": 9, "why": "x", "evidence_ids": ids}})
    with pytest.raises(ValueError, match="sconosciuto"):
        await opportunities.score(op.id, {"magia": {"value": 3}})
    assert opportunities.total(await opportunities.get(op.id)) == (
        None,
        0.0,
        list(opportunities.CRITERIA),
    )

    await opportunities.score(
        op.id,
        {
            "size": {"value": 4, "why": "molti studi", "evidence_ids": ids[:2]},
            "paying": {"value": 5, "why": "pagano già 49 euro", "evidence_ids": ids[2:]},
            "reach": {"value": None, "why": "non so dove si trovano"},
        },
    )
    value, coverage, unknown = opportunities.total(await opportunities.get(op.id))
    assert value == pytest.approx((2 * 4 / 5 + 3 * 5 / 5) / 5 * 100)  # over the known weights only
    assert (
        coverage == pytest.approx(5 / 13, abs=0.01) and "reach" in unknown and "urgency" in unknown
    )


async def test_score_weights_can_be_overridden_from_the_environment(tracer, monkeypatch):
    monkeypatch.setattr(
        opportunities, "get_settings", lambda: SimpleNamespace(score_weights='{"size": 10, "x": 1}')
    )
    w = opportunities.weights()
    assert w["size"] == 10 and "x" not in w and w["paying"] == 3.0
    monkeypatch.setattr(opportunities, "get_settings", lambda: SimpleNamespace(score_weights="boh"))
    assert opportunities.weights()["size"] == 2.0  # a broken override keeps the defaults


async def test_old_evidence_is_flagged_and_confidence_follows_the_evidence(tracer):
    op, ids = await seed(n=5)
    old = await opportunities.add_evidence(
        op.id, "competitor", "https://vecchio.example/p", quote(99), TODAY - timedelta(days=500)
    )
    await opportunities.score(
        op.id,
        {c: {"value": 3, "why": "ok", "evidence_ids": ids[:1]} for c in opportunities.CRITERIA},
    )
    data = await opportunities.summary(op.id)
    assert data["confidence"] == "prove forti" and data["unknown_criteria"] == []
    assert [e["stale"] for e in data["evidence"]] == [False] * 5 + [True]
    assert old.fetched_on < TODAY - timedelta(days=365)
    assert "[VECCHIA]" in opportunities.card(
        data
    ) and "https://forum.example/t/0" in opportunities.card(data)
    assert opportunities.confidence(2, 1.0) == "prove assenti"
    assert opportunities.confidence(3, 0.2) == "prove deboli"


# ---------- the radar ----------


async def make_scored(title, n, value):
    op, ids = await seed(title, n=n)
    if n:
        await opportunities.score(
            op.id, {"paying": {"value": value, "why": "dati", "evidence_ids": ids[:1]}}
        )
    return op


async def test_the_radar_shows_the_three_best_with_their_evidence(tracer):
    a = await make_scored("A forte", 4, 5)
    b = await make_scored("B media", 3, 3)
    c = await make_scored("C debole", 3, 1)
    few = await make_scored("D poche prove", 2, 5)  # only two evidence: never shown
    nosc = (await seed("E senza punteggio", n=4))[0]
    decided = await make_scored("F già decisa", 4, 5)
    await radar.discard(decided.id)
    assert await opportunities.shortlist(3) == [a.id, b.id, c.id]

    sent: list[tuple[str, list[int]]] = []

    async def send(text, ids):
        sent.append((text, ids))

    assert await radar.run_radar(lambda p: None, send, do_scout=False) == [a.id, b.id, c.id]
    assert [ids for _, ids in sent] == [[a.id], [b.id], [c.id]]
    first = sent[0][0]
    assert (
        "A forte" in first
        and "https://forum.example/t/0" in first
        and "«Un commercialista" in first
    )
    assert few.id not in sum((i for _, i in sent), []) and nosc.id not in sum(
        (i for _, i in sent), []
    )
    assert all(text.count("https://forum.example/t/") >= 3 for text, _ in sent[:1])


async def test_the_radar_says_so_when_nothing_has_enough_evidence(tracer):
    await make_scored("Solo due prove", 2, 5)
    sent: list[str] = []

    async def send(text, ids):
        sent.append(text)
        assert ids == []

    assert await radar.run_radar(lambda p: None, send, do_scout=False) == []
    assert "nessuna opportunità" in sent[0]


async def test_scouting_goes_through_the_tools_and_nothing_is_saved_without_a_source(
    tracer, catalog
):
    saves = [
        ToolUse(
            "1",
            "save_opportunity",
            {"title": "Fatture lente", "niche": "studi", "problem": "tempo"},
        ),
    ]
    adds = [
        ToolUse(
            "2", "add_evidence",
            {"opportunity_id": 1, "kind": "complaint", "url": "https://forum.example/x",
             "quote": "Ogni mese perdo mezza giornata con le fatture dei clienti."},
        ),
        ToolUse(  # invented: no address, quote too short
            "3", "add_evidence",
            {"opportunity_id": 1, "kind": "price", "url": "boh", "quote": "costa tanto"},
        ),
    ]  # fmt: skip
    llm = FakeLLMClient(
        [
            LLMResponse(tool_uses=saves),
            LLMResponse(tool_uses=adds),
            LLMResponse(text="Salvata 1 nicchia."),
        ]
    )

    def make_ctx(product):
        return AgentContext(llm=llm, registry=default_registry(), tracer=tracer)

    assert await radar.scout(make_ctx) == 1
    ev = await opportunities.evidence_of(1)
    assert [e.url for e in ev] == ["https://forum.example/x"]  # the invented one was refused


# ---------- verification kit and approval ----------


KIT = "Pagina: titolo, promessa, modulo con consenso. Offerta: 49 euro al mese. " * 3


async def test_a_kit_needs_thresholds_a_future_deadline_and_closing_criteria(tracer):
    op, _ = await seed()
    soon = TODAY + timedelta(days=14)
    kill = "Si chiude se dopo tre settimane ci sono meno di dieci iscritti."
    for args, msg in (
        ((KIT, {"signups": 0}, soon, kill), "soglie"),
        ((KIT, {}, soon, kill), "soglie"),
        ((KIT, {"signups": 30}, TODAY, kill), "futuro"),
        ((KIT, {"signups": 30}, soon, "boh"), "criteri di chiusura"),
    ):
        with pytest.raises(ValueError, match=msg):
            await opportunities.draft_kit(op.id, *args)
    plan = await opportunities.draft_kit(
        op.id, KIT, {"signups": 30, "presales": 3, "bogus": 9}, soon, kill
    )
    assert plan.thresholds == {"signups": 30, "presales": 3} and plan.approved is False
    assert (await opportunities.get(op.id)).status == "shortlisted"


async def test_the_verification_starts_only_after_the_click(tracer):
    op, _ = await seed()
    await opportunities.draft_kit(
        op.id, KIT, {"signups": 30}, TODAY + timedelta(days=14), "Chiudere se meno di 10 iscritti."
    )
    ctx = AgentContext(
        llm=FakeLLMClient(
            [
                LLMResponse(tool_uses=[ToolUse("1", "start_validation", {"opportunity_id": 1})]),
                LLMResponse(text="Avviata."),
            ]
        ),
        registry=default_registry(), tracer=tracer,
    )  # fmt: skip
    result = await run_agent("avvia la verifica", ctx)
    plan = await opportunities.plan_of(op.id)
    assert result.status == "awaiting_confirmation" and plan.approved is False
    assert (await opportunities.get(op.id)).status == "shortlisted"
    assert "signups 30" in result.answer and "Chiudere se meno di 10 iscritti" in result.answer
    await resume_confirmed(ctx, result)
    assert (await opportunities.plan_of(op.id)).approved is True
    assert (await opportunities.get(op.id)).status == "validating"
    fresh, _ = await seed("Un'altra", n=0)
    with pytest.raises(ValueError, match="manca il kit"):
        await opportunities.start_validation(fresh.id)


async def test_the_approve_button_leads_to_a_kit_that_waits_for_the_confirmation(tracer):
    op, _ = await seed()
    kit = ToolUse(
        "1", "draft_validation_kit",
        {"opportunity_id": op.id, "kit": KIT, "thresholds": {"signups": 25},
         "deadline": (TODAY + timedelta(days=20)).isoformat(),
         "kill_criteria": "Chiudere se meno di 8 iscritti entro la scadenza."},
    )  # fmt: skip
    start = ToolUse("2", "start_validation", {"opportunity_id": op.id})
    llm = FakeLLMClient([LLMResponse(tool_uses=[kit, start]), LLMResponse(text="Fatto.")])
    result, ctx = await radar.approve_to_kit(
        op.id, lambda p: AgentContext(llm=llm, registry=default_registry(), tracer=tracer)
    )
    assert result.status == "awaiting_confirmation" and "signups 25" in result.answer
    assert (await opportunities.plan_of(op.id)).approved is False  # nothing started yet


# ---------- decisions ----------


async def test_a_decision_needs_the_click_and_a_go_only_drafts_a_spec(tracer, catalog):
    op, _ = await seed(product="Fatture")
    await opportunities.draft_kit(
        op.id,
        KIT,
        {"signups": 30, "presales": 3},
        TODAY + timedelta(days=14),
        "Chiudere se meno di 10.",
    )
    use = ToolUse(
        "1", "decide_opportunity",
        {"opportunity_id": op.id, "decision": "go", "reason": "Soglie raggiunte, partiamo",
         "results": {"signups": 41, "presales": 2}},
    )  # fmt: skip
    ctx = AgentContext(
        llm=FakeLLMClient([LLMResponse(tool_uses=[use]), LLMResponse(text="Registrato.")]),
        registry=default_registry(), tracer=tracer,
    )  # fmt: skip
    result = await run_agent("costruiamo", ctx)
    assert result.status == "awaiting_confirmation"
    assert (await opportunities.get(op.id)).status == "shortlisted"  # not decided yet
    assert "COSTRUIRE" in result.answer and "soglie NON raggiunte" in result.answer
    await resume_confirmed(ctx, result)
    done = await opportunities.get(op.id)
    assert done.status == "go" and "NON raggiunte" in done.decision_reason

    async with db.session_maker()() as s:
        specs = await s.execute(select(builds.Spec))
        assert [sp.status for sp in specs.scalars()] == ["draft"]  # a draft spec of phase 10...
        assert list((await s.execute(select(BuildJob))).scalars()) == []  # ...and no work started


async def test_go_without_a_product_says_what_is_missing_and_kill_closes(tracer, catalog):
    op, _ = await seed()
    out = await opportunities.decide(op.id, "go", "Partiamo comunque", None)
    assert "products.yaml" in out["next"] and "draft_spec_id" not in out
    other, _ = await seed("Da chiudere")
    assert (await opportunities.decide(other.id, "kill", "Nessun interesse"))["decision"] == "kill"
    assert (await opportunities.get(other.id)).status == "killed"
    with pytest.raises(ValueError, match="decisione non valida"):
        await opportunities.decide(op.id, "forse", "boh")
    with pytest.raises(Exception, match="decision"):
        decide_opportunity.DecideInput(opportunity_id=1, decision="forse", reason="x" * 12)


async def test_the_score_tool_accepts_unknown_but_not_a_number_without_evidence(tracer):
    op, ids = await seed()
    ok = await score_opportunity.score_opportunity(
        score_opportunity.ScoreInput(
            opportunity_id=op.id, scores={"size": {"value": None, "why": "non trovato"}}
        )
    )
    assert "size" in ok["unknown_criteria"] and ok["score"] is None
    with pytest.raises(ValueError, match="senza prove"):
        await score_opportunity.score_opportunity(
            score_opportunity.ScoreInput(
                opportunity_id=op.id, scores={"size": {"value": 5, "why": "x"}}
            )
        )


# ---------- the workspace and the tool list ----------


async def test_the_niche_workspace_is_known_to_the_advisor(tracer, catalog):
    assert advisor.canonical("nicchie") == "Nicchie"
    await make_scored("Fatture", 3, 4)
    text = await advisor.context_for("Nicchie")
    assert "Fatture" in text and "3 prove" in text and text.startswith("Opportunities under study")
    assert await advisor.context_for("Fatture") == ""  # a real product has no decisions yet


def test_the_niche_tools_belong_to_the_advisor_not_to_everyday_chats():
    names = {
        "save_opportunity", "add_evidence", "score_opportunity", "list_opportunities",
        "get_opportunity", "draft_validation_kit", "start_validation", "decide_opportunity",
    }  # fmt: skip
    assert names <= ADVISOR_ONLY
    assert not names & {d["name"] for d in everyday_registry().definitions()}
    full = {d["name"]: d for d in default_registry().definitions()}
    assert names <= set(full)
    perms = {t.name: t.permission for t in default_registry()._tools.values()}
    assert perms["start_validation"] == perms["decide_opportunity"] == "confirm"
    assert perms["add_evidence"] == perms["score_opportunity"] == "write"
    assert perms["get_opportunity"] == perms["list_opportunities"] == "read"
    assert date.today()  # (keeps the import used)


def test_each_advisor_workspace_gets_its_own_smaller_tool_list():
    import json

    from app.tools.builtin import NICHE_TOOLS, advisor_registry

    niches = {d["name"] for d in advisor_registry("Nicchie").definitions()}
    product = {d["name"] for d in advisor_registry("Jarvis").definitions()}
    assert NICHE_TOOLS <= niches and not NICHE_TOOLS & product
    assert "web_search" in niches and "merge_pull_request" not in niches  # only what it needs
    assert {"search_docs", "log_decision", "draft_spec"} <= product
    size = lambda r: len(json.dumps(r.definitions())) // 4  # noqa: E731
    assert size(advisor_registry("Nicchie")) < 3300 and size(advisor_registry("Jarvis")) < 4500
