from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.llm import FakeLLMClient, LLMResponse, ToolUse
from app.interfaces.telegram import BotState, build_handlers
from app.report import REPORT_PROMPT, generate_report
from app.tools.registry import Tool, ToolRegistry


def _registry(calls: list[str]) -> ToolRegistry:
    from pydantic import BaseModel

    class Empty(BaseModel):
        product: str | None = None

    def fake(name: str) -> Tool:
        async def run(inp):
            calls.append(name)
            return {"products": []}

        return Tool(name, "fake", Empty, "read", run)

    reg = ToolRegistry()
    reg.register(fake("site_status"))
    reg.register(fake("github_activity"))
    return reg


def _llm() -> FakeLLMClient:
    uses = [ToolUse("1", "site_status", {}), ToolUse("2", "github_activity", {})]
    return FakeLLMClient([LLMResponse(tool_uses=uses), LLMResponse(text="Tutto regolare.")])


async def test_report_calls_both_tools_and_traces_them(tracer, make_ctx):
    calls: list[str] = []
    result = await generate_report(make_ctx(_llm(), _registry(calls)))
    assert result.status == "completed" and result.answer == "Tutto regolare."
    assert calls == ["site_status", "github_activity"]
    run = await tracer.get_run(result.run_id)
    assert [s.tool_name for s in run.steps if s.type == "tool_call"] == calls
    assert run.user_message == REPORT_PROMPT


def _update(user_id: int):
    msg = SimpleNamespace(text="/report", reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_message=msg,
        effective_chat=SimpleNamespace(id=user_id, send_action=AsyncMock()),
    )
    return update, msg


def _report_handler(state):
    return next(h for h in build_handlers(state) if "report" in getattr(h, "commands", ()))


async def test_report_command_replies_for_allowed_user(tracer, make_ctx):
    state = BotState({1}, lambda: make_ctx(_llm(), _registry([])), tracer)
    update, msg = _update(1)
    await _report_handler(state).callback(update, None)
    msg.reply_text.assert_awaited_once_with("Tutto regolare.")


async def test_report_command_ignores_unauthorized_user(tracer, make_ctx):
    state = BotState({1}, lambda: make_ctx(FakeLLMClient([])), tracer)
    update, msg = _update(999)
    await _report_handler(state).callback(update, None)
    msg.reply_text.assert_not_called()
