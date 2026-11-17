from app.agent.llm import FakeLLMClient, LLMResponse
from evals.compare import compare, latest_result, save_result
from evals.judge import judge_answer, parse_verdict
from evals.scenario import SCENARIOS_DIR, load_scenario


def _row(name, ok=True, score=None):
    return {"name": name, "ok": ok, "score": score}


def test_every_shipped_scenario_is_valid_and_unique():
    scenarios = [load_scenario(p) for p in sorted(SCENARIOS_DIR.glob("*.yaml"))]
    assert len(scenarios) >= 11
    assert len({s.name for s in scenarios}) == len(scenarios)


def test_parse_verdict_accepts_plain_and_fenced_json():
    assert parse_verdict('{"score": 0.8, "reason": "ok"}') == (0.8, "ok")
    assert parse_verdict('```json\n{"score": 1, "reason": "yes"}\n```') == (1.0, "yes")


def test_parse_verdict_clamps_and_rejects_garbage():
    assert parse_verdict('{"score": 7, "reason": "x"}')[0] == 1.0
    score, reason = parse_verdict("I think it is fine")
    assert score == 0.0 and "unparseable" in reason
    assert parse_verdict('{"reason": "no score"}')[0] == 0.0


async def test_judge_is_traced_and_returns_the_score(tracer):
    llm = FakeLLMClient(
        [LLMResponse(text='{"score": 0.6, "reason": "partial"}', input_tokens=50, output_tokens=9)]
    )
    v = await judge_answer(llm, tracer, "s1", "q?", "a.", "must say a")
    assert (v.score, v.reason, v.input_tokens, v.output_tokens) == (0.6, "partial", 50, 9)
    run = (await tracer.list_runs())[0]
    assert run.user_message == "[judge] s1" and run.status == "completed"
    assert run.steps[0].type == "llm_call" and run.steps[0].output["score"] == 0.6
    assert "must say a" in llm.calls[0][0]["content"]


def test_compare_reports_regressions_fixes_and_new():
    prev = {"results": [_row("a"), _row("b", ok=False), _row("c", score=0.9), _row("d", score=0.9)]}
    cur = [_row("a", ok=False), _row("b"), _row("c", score=0.5), _row("d", score=0.85), _row("e")]
    diff = compare(prev, cur)
    assert diff["regressions"] == ["a: PASS -> FAIL", "c: score 0.90 -> 0.50"]
    assert diff["fixed"] == ["b"] and diff["new"] == ["e"]


def test_compare_without_previous_run_reports_nothing():
    assert compare(None, [_row("a")]) == {"regressions": [], "fixed": [], "new": []}


def test_results_roundtrip_returns_the_latest(tmp_path):
    assert latest_result(tmp_path) is None
    save_result(tmp_path, "20260101-000000", "m", [_row("old")])
    save_result(tmp_path, "20260102-000000", "m", [_row("new")])
    assert latest_result(tmp_path)["results"][0]["name"] == "new"


def test_eval_modules_import_and_poisoned_repo_tool_exists():
    # a syntax slip in the eval fixtures once went unnoticed: importing them is part of the suite
    from evals import fixture_tools, run  # noqa: F401

    assert "read_repo_file_demo" in fixture_tools.EXTRA_TOOLS


async def test_a_scenario_that_blows_up_fails_alone_and_the_run_goes_on(monkeypatch):
    from evals import run as eval_run
    from evals.scenario import Scenario

    row = eval_run._errored(Scenario(name="x", input="ciao"), RuntimeError("429 Too Many Requests"))
    assert row["ok"] is False and "429" in row["why"][0] and row["name"] == "x"
