from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.llm import FakeLLMClient
from app.interfaces.telegram import BotState, build_handlers


async def test_unauthorized_user_is_ignored(tracer, make_ctx, caplog):
    state = BotState({1}, lambda: make_ctx(FakeLLMClient([])), tracer)
    on_text = build_handlers(state)[-1].callback
    msg = SimpleNamespace(text="ciao", reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=999),
        effective_message=msg,
        effective_chat=SimpleNamespace(id=999, send_action=AsyncMock()),
    )
    with caplog.at_level("WARNING"):
        await on_text(update, None)
    msg.reply_text.assert_not_called()
    assert "unauthorized" in caplog.text
    assert 999 not in state.contexts
