import asyncio

from pydantic import BaseModel

from app.agent.llm import FakeLLMClient, LLMResponse, ToolUse
from app.agent.loop import cancel_pending, resume_confirmed, run_agent
from app.tools.registry import Tool, ToolRegistry


def tu(name="get_current_time", i="1", **inp):
    return LLMResponse(tool_uses=[ToolUse(i, name, inp)], input_tokens=5, output_tokens=2)


async def test_direct_answer(make_ctx, tracer):
    ctx = make_ctx(FakeLLMClient([LLMResponse(text="ciao", input_tokens=3, output_tokens=1)]))
    r = await run_agent("hi", ctx)
    assert r.status == "completed" and r.answer == "ciao"
    run = await tracer.get_run(r.run_id)
    assert (run.input_tokens, run.output_tokens) == (3, 1)


async def test_single_tool_call(make_ctx, tracer):
    llm = FakeLLMClient([tu(timezone="Asia/Tokyo"), LLMResponse(text="sono le...")])
    r = await run_agent("ore a Tokyo?", make_ctx(llm))
    run = await tracer.get_run(r.run_id)
    assert [s.type for s in run.steps] == ["llm_call", "tool_call", "llm_call"]
    assert run.steps[1].tool_name == "get_current_time" and run.steps[1].error is None


async def test_multiple_tool_calls_in_sequence(make_ctx, tracer):
    llm = FakeLLMClient([tu(i="1"), tu(i="2", timezone="UTC"), LLMResponse(text="fatto")])
    r = await run_agent("x", make_ctx(llm))
    run = await tracer.get_run(r.run_id)
    assert sum(s.type == "tool_call" for s in run.steps) == 2 and r.answer == "fatto"


async def test_tool_error_is_returned_to_model(make_ctx, tracer):
    llm = FakeLLMClient([tu(timezone="Not/AZone"), LLMResponse(text="errore gestito")])
    r = await run_agent("x", make_ctx(llm))
    assert r.status == "completed"
    run = await tracer.get_run(r.run_id)
    assert run.steps[1].error
    assert llm.calls[1][-1]["content"][0]["is_error"] is True


async def test_invalid_input_returns_readable_error(make_ctx, tracer):
    llm = FakeLLMClient([tu("http_health_check"), LLMResponse(text="ok")])
    r = await run_agent("x", make_ctx(llm))
    run = await tracer.get_run(r.run_id)
    assert "Invalid input" in run.steps[1].error


async def test_tool_timeout(make_ctx, tracer):
    class In(BaseModel):
        pass

    async def slow(_: In) -> int:
        await asyncio.sleep(5)

    reg = ToolRegistry()
    reg.register(Tool("slow", "slow", In, "read", slow))
    llm = FakeLLMClient([tu("slow"), LLMResponse(text="ok")])
    r = await run_agent("x", make_ctx(llm, reg, tool_timeout=0.05))
    run = await tracer.get_run(r.run_id)
    assert "timed out" in run.steps[1].error and r.status == "completed"


async def test_max_iterations(make_ctx):
    llm = FakeLLMClient([tu(i=str(n)) for n in range(10)])
    r = await run_agent("x", make_ctx(llm, max_iterations=3))
    assert r.status == "max_iterations" and len(llm.calls) == 3


async def test_confirm_tool_not_executed_without_confirmation(make_ctx, tracer):
    executed = []

    class In(BaseModel):
        text: str

    async def danger(i: In) -> str:
        executed.append(i.text)
        return "done"

    reg = ToolRegistry()
    reg.register(Tool("danger", "d", In, "confirm", danger))
    llm = FakeLLMClient([tu("danger", text="x")])
    ctx = make_ctx(llm, reg)
    r = await run_agent("fai", ctx)
    assert r.status == "awaiting_confirmation" and r.pending and executed == []
    cancel_pending(ctx, r)
    assert executed == []
    r2 = await run_agent("fai", make_ctx(FakeLLMClient([tu("danger", text="y")]), reg))
    res = await resume_confirmed(make_ctx(FakeLLMClient([]), reg), r2)
    assert executed == ["y"] and res.status == "completed"


async def test_history_limited_to_20(make_ctx):
    ctx = make_ctx(FakeLLMClient([LLMResponse(text="a")] * 30))
    for n in range(30):
        await run_agent(f"m{n}", ctx)
    assert len(ctx.history) <= 20 and ctx.history[0]["role"] == "user"
