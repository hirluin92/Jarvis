from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import BaseModel
from telegram.ext import CallbackQueryHandler

from app.agent.llm import FakeLLMClient, LLMResponse, ToolUse
from app.agent.loop import (
    CONFIRM_TTL_SECONDS,
    AgentResult,
    PendingAction,
    resume_confirmed,
    run_agent,
)
from app.interfaces.telegram import BotState, build_handlers
from app.tools.registry import Tool, ToolRegistry


class Arg(BaseModel):
    text: str = "x"


def _registry(permission: str, executed: list[str], describe=None) -> ToolRegistry:
    async def run(inp: Arg) -> dict:
        executed.append(inp.text)
        return {"proof": f"done:{inp.text}"}

    reg = ToolRegistry()
    reg.register(Tool("act", "an action", Arg, permission, run, describe))
    return reg


def _call_then(text: str) -> FakeLLMClient:
    use = ToolUse("1", "act", {"text": "hello"})
    return FakeLLMClient([LLMResponse(tool_uses=[use]), LLMResponse(text=text)])


async def test_confirm_tool_never_runs_without_a_click(tracer, make_ctx):
    executed: list[str] = []
    ctx = make_ctx(_call_then("ok"), _registry("confirm", executed))
    result = await run_agent("fai l'azione", ctx)
    assert executed == []  # the tool function was never called
    assert result.status == "awaiting_confirmation" and result.pending is not None
    assert await tracer.list_actions() == []  # nothing executed, nothing logged as an action


async def test_confirmation_message_uses_the_tool_summary(tracer, make_ctx):
    reg = _registry("confirm", [], describe=lambda inp: f"scrivere {inp.text!r} nel registro")
    result = await run_agent("vai", make_ctx(_call_then("ok"), reg))
    assert result.answer == "Conferma richiesta: scrivere 'hello' nel registro"


async def test_confirmed_action_runs_once_and_is_logged_with_its_proof(tracer, make_ctx):
    executed: list[str] = []
    ctx = make_ctx(_call_then("ok"), _registry("confirm", executed))
    ctx.actor = "telegram:42"
    result = await run_agent("vai", ctx)
    done = await resume_confirmed(ctx, result)
    assert executed == ["hello"] and done.status == "completed"
    (action,) = await tracer.list_actions()
    assert (action.actor, action.tool_name, action.permission) == ("telegram:42", "act", "confirm")
    assert action.output == {"proof": "done:hello"} and action.error is None


async def test_write_tool_runs_directly_and_is_logged(tracer, make_ctx):
    executed: list[str] = []
    ctx = make_ctx(_call_then("fatto"), _registry("write", executed))
    result = await run_agent("annota", ctx)
    assert result.status == "completed" and executed == ["hello"]
    (action,) = await tracer.list_actions()
    assert action.permission == "write" and action.output == {"proof": "done:hello"}


async def test_read_tool_is_not_an_action(tracer, make_ctx):
    ctx = make_ctx(_call_then("ok"), _registry("read", []))
    await run_agent("leggi", ctx)
    assert await tracer.list_actions() == []


async def test_failed_action_is_logged_with_its_error(tracer, make_ctx):
    async def boom(inp: Arg) -> dict:
        raise RuntimeError("github down")

    reg = ToolRegistry()
    reg.register(Tool("act", "an action", Arg, "write", boom))
    await run_agent("annota", make_ctx(_call_then("non riuscito"), reg))
    (action,) = await tracer.list_actions()
    assert action.output is None and "github down" in action.error


def test_pending_expires_after_ten_minutes():
    p = PendingAction("act", {}, "id", created_at=1000.0)
    assert not p.expired(now=1000.0 + CONFIRM_TTL_SECONDS)
    assert p.expired(now=1000.0 + CONFIRM_TTL_SECONDS + 1)
    assert CONFIRM_TTL_SECONDS == 600


def _button(user_id: int, data: str):
    q = SimpleNamespace(
        from_user=SimpleNamespace(id=user_id),
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=user_id))
    return update, q


def _on_button(state):
    return next(h for h in build_handlers(state) if isinstance(h, CallbackQueryHandler)).callback


async def _state_with_pending(tracer, make_ctx, executed, age_seconds=0.0):
    ctx = make_ctx(_call_then("ok"), _registry("confirm", executed))
    state = BotState({1}, lambda: ctx, tracer)
    result = await run_agent("vai", await state.ctx(1))
    result.pending.created_at -= age_seconds
    state.pending[1] = result
    return state, result


async def test_click_on_confirm_executes_exactly_once(tracer, make_ctx):
    executed: list[str] = []
    state, result = await _state_with_pending(tracer, make_ctx, executed)
    update, q = _button(1, f"ok:{result.run_id}")
    await _on_button(state)(update, None)
    assert executed == ["hello"]
    update2, q2 = _button(1, f"ok:{result.run_id}")  # second click on the same confirmation
    await _on_button(state)(update2, None)
    assert executed == ["hello"]
    q2.edit_message_text.assert_awaited_once_with("Richiesta scaduta o già usata.")


async def test_expired_confirmation_is_refused(tracer, make_ctx):
    executed: list[str] = []
    state, result = await _state_with_pending(
        tracer, make_ctx, executed, age_seconds=CONFIRM_TTL_SECONDS + 5
    )
    update, q = _button(1, f"ok:{result.run_id}")
    await _on_button(state)(update, None)
    assert executed == []
    assert "scaduta" in q.edit_message_text.await_args.args[0]
    assert await tracer.list_actions() == []


async def test_cancel_does_not_execute(tracer, make_ctx):
    executed: list[str] = []
    state, result = await _state_with_pending(tracer, make_ctx, executed)
    update, q = _button(1, f"no:{result.run_id}")
    await _on_button(state)(update, None)
    assert executed == []
    q.edit_message_text.assert_awaited_once_with("Azione annullata.")


async def test_button_from_unauthorized_user_does_nothing(tracer, make_ctx):
    executed: list[str] = []
    state, result = await _state_with_pending(tracer, make_ctx, executed)
    update, q = _button(999, f"ok:{result.run_id}")
    await _on_button(state)(update, None)
    assert executed == [] and state.pending.get(1) is result


async def test_actions_command_lists_the_log(tracer, make_ctx):
    await tracer.record_action("telegram:1", None, "create_note", "write", {}, {"id": 5}, None)
    await tracer.record_action(
        "telegram:1", None, "merge_pull_request", "confirm", {}, None, "boom"
    )
    state = BotState({1}, lambda: make_ctx(FakeLLMClient([])), tracer)
    handler = next(h for h in build_handlers(state) if "actions" in getattr(h, "commands", ()))
    msg = SimpleNamespace(reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_message=msg,
        effective_chat=SimpleNamespace(id=1),
    )
    await handler.callback(update, None)
    text = msg.reply_text.await_args.args[0]
    assert "create_note [write] ok" in text and '"id": 5' in text
    assert "merge_pull_request [confirm] ERRORE boom" in text


def test_agent_result_type_is_exported():
    assert AgentResult(1, "completed", "x").pending is None
