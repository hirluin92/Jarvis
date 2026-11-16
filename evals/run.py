import asyncio
import sys
import tempfile
import time
from pathlib import Path

import yaml

from app.agent.llm import AnthropicClient
from app.agent.loop import AgentContext, run_agent
from app.config import get_settings
from app.db.models import Base
from app.db.session import init_db, session_maker
from app.tools.builtin import default_registry
from app.tracing import Tracer

SCENARIOS = Path(__file__).parent / "scenarios"
FIXTURES = Path(__file__).parent / "fixtures"


async def run_scenario(
    sc: dict, tracer: Tracer, llm: AnthropicClient, s, default_catalog: str
) -> dict:
    # a scenario may point at a fixture catalog (e.g. one with a site that is down)
    s.products_file = str(FIXTURES / sc["catalog"]) if "catalog" in sc else default_catalog
    ctx = AgentContext(
        llm=llm,
        registry=default_registry(),
        tracer=tracer,
        max_iterations=s.agent_max_iterations,
        tool_timeout=s.tool_timeout_seconds,
    )
    t0 = time.monotonic()
    result = await run_agent(sc["input"], ctx)
    run = await tracer.get_run(result.run_id)
    called = [x.tool_name for x in run.steps if x.type == "tool_call"]
    ok = result.status == "completed"
    ok &= all(t in called for t in sc.get("expected_tools", []))
    ok &= not any(t in called for t in sc.get("forbidden_tools", []))
    for needle in sc.get("answer_must_contain", []) or []:
        ok &= needle.lower() in result.answer.lower()
    return {
        "name": sc["name"],
        "ok": bool(ok),
        "tools": called,
        "secs": time.monotonic() - t0,
        "tokens": run.input_tokens + run.output_tokens,
    }


async def main() -> int:
    s = get_settings()
    default_catalog = s.products_file
    db_file = Path(tempfile.mkdtemp()) / "evals.db"  # isolated: evals never touch the real DB
    init_db(f"sqlite+aiosqlite:///{db_file}")
    from app.db.session import engine

    async with engine().begin() as c:
        await c.run_sync(Base.metadata.create_all)
    tracer = Tracer(session_maker())
    llm = AnthropicClient(s.anthropic_api_key, s.anthropic_model)
    rows = [
        await run_scenario(
            yaml.safe_load(p.read_text(encoding="utf-8")), tracer, llm, s, default_catalog
        )
        for p in sorted(SCENARIOS.glob("*.yaml"))
    ]
    print(f"{'scenario':<20}{'result':<8}{'tools':<30}{'secs':>6}{'tokens':>8}")
    for r in rows:
        print(
            f"{r['name']:<20}{'PASS' if r['ok'] else 'FAIL':<8}{','.join(r['tools']) or '-':<30}"
            f"{r['secs']:>6.1f}{r['tokens']:>8}"
        )
    return 0 if all(r["ok"] for r in rows) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
