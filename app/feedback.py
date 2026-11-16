from pathlib import Path

from app.tracing import Tracer
from evals.scenario import DRAFTS_DIR, Scenario, write_scenario


async def mark_bad_and_draft(
    tracer: Tracer, run_id: int, directory: Path = DRAFTS_DIR
) -> Path | None:
    """Flag a run as wrong and write a draft eval scenario from it. None if the run is unknown."""
    if not await tracer.mark_bad(run_id):
        return None
    run = await tracer.get_run(run_id)
    assert run is not None
    called = [s.tool_name for s in run.steps if s.type == "tool_call" and s.tool_name]
    scenario = Scenario(
        name=f"bad_{run_id}",
        input=run.user_message,
        # starting point only: replace with what SHOULD have happened
        expected_tools=list(dict.fromkeys(called)),
        note=(
            f"Draft from run {run_id}, marked wrong by the user. TO COMPLETE: fix expected_tools, "
            f"add forbidden_tools / answer_must_contain, then move to evals/scenarios/. "
            f"Answer given: {(run.final_answer or '')[:300]!r}"
        ),
    )
    return write_scenario(scenario, directory)
