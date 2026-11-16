from datetime import UTC, datetime, timedelta

import pytest

from app.agent.llm import FakeLLMClient, LLMResponse
from app.costs import estimate_cost, format_cost
from app.report import cost_summary, generate_report


def test_estimate_cost_from_token_prices():
    # sonnet 5.5: $2 in / $10 out per million tokens
    assert estimate_cost("claude-sonnet-5-5", 1_000_000, 0) == pytest.approx(2.0)
    assert estimate_cost("claude-sonnet-5-5", 0, 1_000_000) == pytest.approx(10.0)
    assert estimate_cost("claude-sonnet-5-5", 2720, 351) == pytest.approx(0.00895)


def test_unknown_model_has_no_cost():
    assert estimate_cost("fake", 100, 100) is None
    assert format_cost(None) == "n/d"
    assert format_cost(0.0123456) == "$0.0123"


async def test_runs_since_filters_by_start_time(tracer):
    await tracer.start_run("old", "m")
    since = datetime.now(UTC) - timedelta(seconds=1)
    new_id = await tracer.start_run("new", "m")
    runs = await tracer.runs_since(since)
    assert [r.id for r in runs][-1] == new_id


async def test_cost_summary_sums_priced_runs_only(tracer):
    for model, tin, tout in [("claude-sonnet-5-5", 1_000_000, 0), ("fake", 5, 5)]:
        rid = await tracer.start_run("q", model)
        await tracer.finish_run(rid, "completed", "a", tin, tout)
    line = await cost_summary(tracer)
    assert line == "Costi stimati ultime 24h: $2.0000 (2 run)"


async def test_cost_summary_is_omitted_without_priced_runs(tracer):
    rid = await tracer.start_run("q", "fake")
    await tracer.finish_run(rid, "completed", "a", 5, 5)
    assert await cost_summary(tracer) is None


async def test_report_appends_the_cost_line_computed_in_code(tracer, make_ctx):
    llm = FakeLLMClient([LLMResponse(text="Tutto regolare.", input_tokens=1_000_000)])
    llm.model = "claude-sonnet-5-5"
    result = await generate_report(make_ctx(llm))
    assert result.answer == "Tutto regolare.\n\nCosti stimati ultime 24h: $2.0000 (1 run)"
