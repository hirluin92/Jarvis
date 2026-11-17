import argparse
import asyncio
import json
import sys
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from app import advisor, growth, knowledge, opportunities
from app.agent.llm import RoutedLLM, build_llms
from app.agent.loop import AgentContext, run_agent
from app.agent.prompts import ADVISOR_STYLE, VOICE_STYLE
from app.config import get_settings
from app.costs import estimate_cost
from app.db.models import (
    Base,
    Decision,
    Evidence,
    GrowthState,
    InboundMessage,
    KnowledgeChunk,
    Lead,
    Metric,
    Opportunity,
    OutreachItem,
    ProductBrief,
    Suppression,
    ValidationPlan,
)
from app.db.session import init_db, session_maker
from app.memory import MemoryStore
from app.report import REPORT_PROMPT
from app.tools.builtin import advisor_registry, everyday_registry
from app.tracing import Tracer
from evals.compare import compare, latest_result, save_result
from evals.fixture_tools import make_eval_registry
from evals.judge import judge_answer
from evals.scenario import SCENARIOS_DIR, Scenario, load_scenario

FIXTURES = Path(__file__).parent / "fixtures"
RESULTS = Path(__file__).parent / "results"


async def seed_advisor_data(sc: Scenario) -> None:
    """Documents, briefs and decisions of the scenario; the previous scenario's are removed."""
    from sqlalchemy import delete

    async with session_maker()() as s:
        for model in (
            KnowledgeChunk,
            ProductBrief,
            Decision,
            Evidence,
            ValidationPlan,
            Opportunity,
            OutreachItem,
            InboundMessage,
            Lead,
            Suppression,
            Metric,
            GrowthState,
        ):
            await s.execute(delete(model))
        await s.commit()
    for d in sc.docs:
        await knowledge.import_file(d["product"], d["source"], d["text"])
    for b in sc.briefs:
        await advisor.update_brief(b["product"], **{k: v for k, v in b.items() if k != "product"})
    for o in sc.opportunities:
        op = await opportunities.save(o["title"], o.get("niche", ""), o.get("problem", ""))
        ids = []
        for e in o.get("evidence", []):
            row = await opportunities.add_evidence(
                op.id, e["kind"], e["url"], e["quote"], date.fromisoformat(str(e["fetched_on"]))
            )
            ids.append(row.id)
        for name, sc_item in o.get("scores", {}).items():
            n = sc_item.get("evidence_index")
            item = {"value": sc_item.get("value"), "why": sc_item.get("why", "dati")}
            item["evidence_ids"] = [ids[n]] if n is not None else []
            await opportunities.score(op.id, {name: item})
        if "plan" in o:
            p = o["plan"]
            await opportunities.draft_kit(
                op.id,
                p["kit"],
                p["thresholds"],
                date.today() + timedelta(days=p.get("days", 14)),
                p["kill_criteria"],
            )
            if o.get("status") == "validating":
                await opportunities.start_validation(op.id)
    for m in sc.inbound:
        row, _ = await growth.store_inbound(
            f"eval-{m['from']}", "t", m["from"], m.get("subject", ""), m["body"]
        )
        if m.get("classification"):
            await growth.set_triage(row.id, m["classification"], m.get("summary", "da leggere"))
    for ld in sc.leads:
        lead = await growth.add_lead(
            ld["contact"], ld.get("source_url", "https://example.org/contatti"),
            ld.get("legal_basis", "indirizzo aziendale pubblicato sul sito"),
            ld.get("consent", "business"), ld.get("name", ""),
        )  # fmt: skip
        for q in ld.get("queue", []):
            await growth.queue(lead.id, q["subject"], q["body"], q.get("kind", "cold"))
    for d in sc.decisions:
        await advisor.log_decision(
            d["product"], d["decision"], d.get("reasons", ""), d.get("alternatives", ""),
            d.get("review_on"),
        )  # fmt: skip


async def run_scenario(sc: Scenario, tracer: Tracer, llm, s, default_catalog: str) -> dict:
    # a scenario may point at a fixture catalog (e.g. one with a site that is down)
    s.products_file = str(FIXTURES / sc.catalog) if sc.catalog else default_catalog
    ctx = AgentContext(
        llm=llm,
        registry=make_eval_registry(
            advisor_registry(sc.advisor_product) if sc.advisor else everyday_registry(),
            sc.extra_tools,
        ),
        tracer=tracer,
        max_iterations=s.agent_max_iterations,
        tool_timeout=s.tool_timeout_seconds,
    )
    memory = MemoryStore(session_maker())
    await memory.wipe()  # scenarios must not see each other's facts
    for fact in sc.memories:
        await memory.remember(fact)
    ctx.system_extra = memory.context_for
    await seed_advisor_data(sc)
    if sc.advisor:
        ctx.style = f"{ADVISOR_STYLE}\n\nThe product you advise on: {sc.advisor_product}."
        ctx.advisor = True
        ctx.system_extra = advisor.with_advisor_data(sc.advisor_product, memory.context_for)
    t0 = time.monotonic()
    question = REPORT_PROMPT if sc.input == "@report" else sc.input
    if sc.routed:  # exactly what a spoken turn does: fast model first, spoken answer style
        _, fast = build_llms(s)
        router = RoutedLLM(fast, llm, ctx.registry.names_with("read"))
        result = await run_agent(question, ctx, style=VOICE_STYLE, llm=router)
    else:
        result = await run_agent(question, ctx)
    run = await tracer.get_run(result.run_id)
    called = [x.tool_name for x in run.steps if x.type == "tool_call"]
    why: list[str] = []  # what went wrong, shown under the table
    if result.status != sc.expected_status:
        why.append(f"status {result.status}, expected {sc.expected_status}")
    why += [f"tool not called: {t}" for t in sc.expected_tools if t not in called]
    why += [f"forbidden tool called: {t}" for t in sc.forbidden_tools if t in called]
    why += [
        f"answer lacks {n!r}"
        for n in sc.answer_must_contain
        if n.lower() not in result.answer.lower()
    ]
    ok = not why
    tin, tout = run.input_tokens, run.output_tokens
    score = None
    if sc.rubric:
        tools_line = "Tools the assistant really has: " + ", ".join(sorted(ctx.registry._tools))
        evidence = "\n".join(
            [tools_line]
            + [
                json.dumps(x.output, ensure_ascii=False, default=str)[:1500]
                for x in run.steps
                if x.type == "tool_call"
            ]
        )
        v = await judge_answer(llm, tracer, sc.name, sc.input, result.answer, sc.rubric, evidence)
        score = v.score
        if score < sc.min_score:
            ok = False
            why.append(f"judge {score:.2f} < {sc.min_score}: {v.reason[:140]}")
        tin, tout = tin + v.input_tokens, tout + v.output_tokens
    return {
        "name": sc.name,
        "ok": bool(ok),
        "score": score,
        "tools": called,
        "why": why,
        "answer": result.answer[:300],
        "secs": time.monotonic() - t0,
        "tokens": tin + tout,
        "cost": estimate_cost(llm.model, tin, tout) or 0.0,
    }


def _skipped(sc: Scenario) -> dict:
    return {"name": sc.name, "ok": False, "score": None, "tools": [], "secs": 0, "tokens": 0,
            "cost": 0.0, "skipped": True}  # fmt: skip


def _errored(sc: Scenario, error: Exception) -> dict:
    """A scenario that could not finish: it fails, with the reason, and the run goes on."""
    why = [f"errore dello strumento di valutazione: {type(error).__name__}: {str(error)[:160]}"]
    return {"name": sc.name, "ok": False, "score": None, "tools": [], "why": why, "answer": "",
            "secs": 0, "tokens": 0, "cost": 0.0}  # fmt: skip


def print_table(rows: list[dict]) -> None:
    print(f"{'scenario':<26}{'result':<8}{'score':>6}{'secs':>7}{'tokens':>8}{'cost $':>9}  tools")
    for r in rows:
        score = "-" if r["score"] is None else f"{r['score']:.2f}"
        result = "SKIP" if r.get("skipped") else "PASS" if r["ok"] else "FAIL"
        print(
            f"{r['name']:<26}{result:<8}{score:>6}{r['secs']:>7.1f}{r['tokens']:>8}"
            f"{r['cost']:>9.4f}  {','.join(r['tools']) or '-'}"
        )
    for r in rows:
        for w in r.get("why", []):
            print(f"  ! {r['name']}: {w}")
    print(f"total cost: ${sum(r['cost'] for r in rows):.4f}")


async def main(max_cost: float | None, only: set[str] | None = None) -> int:
    s = get_settings()
    default_catalog = s.products_file
    db_file = Path(tempfile.mkdtemp()) / "evals.db"  # isolated: evals never touch the real DB
    init_db(f"sqlite+aiosqlite:///{db_file}")
    from app.db.session import engine

    async with engine().begin() as c:
        await c.run_sync(Base.metadata.create_all)
    tracer = Tracer(session_maker())
    llm, _ = build_llms(s)
    scenarios = [load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.yaml"))]
    if only:
        scenarios = [sc for sc in scenarios if sc.name in only]
    rows: list[dict] = []
    spent = 0.0
    for sc in scenarios:
        if max_cost is not None and spent >= max_cost:
            rows.append(_skipped(sc))  # spending cap reached: remaining scenarios count as failed
            continue
        try:
            row = await run_scenario(sc, tracer, llm, s, default_catalog)
        except Exception as e:  # one broken scenario (e.g. a rate limit) must not lose the others
            row = _errored(sc, e)
        spent += row["cost"]
        rows.append(row)
    print_table(rows)

    diff = compare(latest_result(RESULTS), rows)
    save_result(RESULTS, datetime.now().strftime("%Y%m%d-%H%M%S"), llm.model, rows)
    for label, key in (("REGRESSIONI", "regressions"), ("Risolti", "fixed"), ("Nuovi", "new")):
        if diff[key]:
            print(f"{label}: " + "; ".join(diff[key]))
    if any(r.get("skipped") for r in rows):
        print(f"ATTENZIONE: tetto di spesa ${max_cost} raggiunto, scenari saltati.")
    return 0 if all(r["ok"] for r in rows) and not diff["regressions"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default=None, help="comma separated scenario names")
    parser.add_argument("--max-cost", type=float, default=None, help="USD cap for this run")
    args = parser.parse_args()
    only = set(args.only.split(",")) if args.only else None
    sys.exit(asyncio.run(main(args.max_cost, only)))
