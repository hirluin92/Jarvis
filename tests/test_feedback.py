from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.llm import FakeLLMClient
from app.feedback import mark_bad_and_draft
from app.interfaces.telegram import BotState, build_handlers
from evals.scenario import load_scenario


async def _run_with_tool(tracer) -> int:
    rid = await tracer.start_run("che ora è a Tokyo?", "fake")
    await tracer.add_step(rid, 0, "llm_call")
    await tracer.add_step(rid, 1, "tool_call", tool_name="get_current_time", input={})
    await tracer.add_step(rid, 2, "tool_call", tool_name="get_current_time", input={})
    await tracer.finish_run(rid, "completed", "Sono le 3", 1, 1)
    return rid


async def test_bad_marks_the_run_and_writes_a_valid_scenario(tracer, tmp_path):
    rid = await _run_with_tool(tracer)
    path = await mark_bad_and_draft(tracer, rid, tmp_path)
    assert path == tmp_path / f"bad_{rid}.yaml"
    sc = load_scenario(path)  # the draft must pass the same validation as shipped scenarios
    assert sc.input == "che ora è a Tokyo?"
    assert sc.expected_tools == ["get_current_time"]  # deduplicated starting point
    assert "TO COMPLETE" in sc.note and "Sono le 3" in sc.note
    assert (await tracer.get_run(rid)).marked_bad is True


async def test_bad_on_unknown_run_does_nothing(tracer, tmp_path):
    drafts = tmp_path / "drafts"
    assert await mark_bad_and_draft(tracer, 999, drafts) is None
    assert not drafts.exists()


def _update(user_id: int):
    msg = SimpleNamespace(text="/bad", reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_message=msg,
        effective_chat=SimpleNamespace(id=user_id, send_action=AsyncMock()),
    )
    return update, msg


def _bad_handler(state):
    return next(h for h in build_handlers(state) if "bad" in getattr(h, "commands", ()))


async def test_bad_command_without_a_previous_run(tracer, make_ctx):
    state = BotState({1}, lambda: make_ctx(FakeLLMClient([])), tracer)
    update, msg = _update(1)
    await _bad_handler(state).callback(update, None)
    msg.reply_text.assert_awaited_once_with("Nessun run recente da segnalare.")


async def test_bad_command_ignores_unauthorized_user(tracer, make_ctx):
    state = BotState({1}, lambda: make_ctx(FakeLLMClient([])), tracer)
    update, msg = _update(999)
    await _bad_handler(state).callback(update, None)
    msg.reply_text.assert_not_called()
