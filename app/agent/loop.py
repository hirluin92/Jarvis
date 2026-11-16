import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from app.agent.llm import LLMClient
from app.agent.prompts import SYSTEM_PROMPT
from app.tools.registry import ToolInputError, ToolRegistry
from app.tracing import Tracer


@dataclass
class PendingAction:
    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str

    def describe(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.tool_input.items())
        return f"{self.tool_name}({args})"


@dataclass
class AgentResult:
    run_id: int
    status: str
    answer: str
    pending: PendingAction | None = None


@dataclass
class AgentContext:
    llm: LLMClient
    registry: ToolRegistry
    tracer: Tracer
    history: list[dict[str, Any]] = field(default_factory=list)
    max_iterations: int = 8
    tool_timeout: float = 30
    history_limit: int = 20


def _trim(history: list[dict[str, Any]], limit: int) -> None:
    del history[:-limit]
    # never start with a tool_result/orphan assistant turn
    while history and not (history[0]["role"] == "user" and isinstance(history[0]["content"], str)):
        history.pop(0)


async def _run_tool(ctx: AgentContext, name: str, raw: dict[str, Any]) -> tuple[Any, str | None]:
    if ctx.registry.get(name) is None:
        return None, f"Unknown tool: {name}"
    try:
        out = await asyncio.wait_for(ctx.registry.execute(name, raw), ctx.tool_timeout)
        return out, None
    except ToolInputError as e:
        return None, str(e)
    except TimeoutError:
        return None, f"Tool {name} timed out after {ctx.tool_timeout}s"
    except Exception as e:  # noqa: BLE001 - tool errors go back to the model
        return None, f"{type(e).__name__}: {e}"


async def _loop(
    ctx: AgentContext,
    run_id: int,
    messages: list[dict[str, Any]],
    idx: int,
    tokens: list[int],
    max_iter: int,
) -> AgentResult:
    for _ in range(max_iter):
        t0 = time.monotonic()
        resp = await ctx.llm.complete(SYSTEM_PROMPT, messages, ctx.registry.definitions())
        tokens[0] += resp.input_tokens
        tokens[1] += resp.output_tokens
        await ctx.tracer.add_step(
            run_id,
            idx,
            "llm_call",
            input={"messages": len(messages)},
            output={"text": resp.text, "tool_uses": [t.name for t in resp.tool_uses]},
            duration_ms=int((time.monotonic() - t0) * 1000),
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
        )
        idx += 1
        messages.append({"role": "assistant", "content": resp.to_assistant_content()})
        if not resp.tool_uses:
            return await _finish(ctx, run_id, "completed", resp.text, tokens)

        results: list[dict[str, Any]] = []
        for tu in resp.tool_uses:
            tool = ctx.registry.get(tu.name)
            if tool and tool.permission == "confirm":
                pending = PendingAction(tu.name, tu.input, tu.id)
                await ctx.tracer.add_step(
                    run_id,
                    idx,
                    "tool_call",
                    tool_name=tu.name,
                    input=tu.input,
                    error="awaiting confirmation",
                )
                msg = f"Conferma richiesta: {pending.describe()}"
                r = await _finish(ctx, run_id, "awaiting_confirmation", msg, tokens)
                r.pending = pending
                return r
            t0 = time.monotonic()
            out, err = await _run_tool(ctx, tu.name, tu.input)
            await ctx.tracer.add_step(
                run_id,
                idx,
                "tool_call",
                tool_name=tu.name,
                input=tu.input,
                output=out,
                error=err,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            idx += 1
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "is_error": err is not None,
                    "content": err if err else str(out),
                }
            )
        messages.append({"role": "user", "content": results})
    return await _finish(
        ctx,
        run_id,
        "max_iterations",
        "Mi sono fermato: raggiunto il numero massimo di passaggi.",
        tokens,
    )


async def _finish(
    ctx: AgentContext, run_id: int, status: str, answer: str, tokens: list[int]
) -> AgentResult:
    await ctx.tracer.finish_run(run_id, status, answer, tokens[0], tokens[1])
    return AgentResult(run_id, status, answer)


async def run_agent(user_message: str, context: AgentContext) -> AgentResult:
    run_id = await context.tracer.start_run(user_message, context.llm.model)
    context.history.append({"role": "user", "content": user_message})
    _trim(context.history, context.history_limit)
    try:
        result = await _loop(context, run_id, context.history, 0, [0, 0], context.max_iterations)
    except Exception as e:
        await context.tracer.finish_run(run_id, "failed", f"{type(e).__name__}: {e}", 0, 0)
        return AgentResult(run_id, "failed", "Errore interno durante l'elaborazione.")
    _trim(context.history, context.history_limit)
    return result


async def resume_confirmed(context: AgentContext, result: AgentResult) -> AgentResult:
    """Execute a pending confirm-level action after explicit user approval and finish the run."""
    p = result.pending
    assert p is not None
    run_id = await context.tracer.start_run(f"[confirmed] {p.describe()}", context.llm.model)
    out, err = await _run_tool(context, p.tool_name, p.tool_input)
    await context.tracer.add_step(
        run_id, 0, "tool_call", tool_name=p.tool_name, input=p.tool_input, output=out, error=err
    )
    context.history.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": p.tool_use_id,
                    "is_error": err is not None,
                    "content": err if err else str(out),
                }
            ],
        }
    )
    answer = f"Eseguito: {out}" if err is None else f"Errore: {err}"
    context.history.append({"role": "assistant", "content": answer})
    await context.tracer.finish_run(run_id, "failed" if err else "completed", answer, 0, 0)
    return AgentResult(run_id, "failed" if err else "completed", answer)


def cancel_pending(context: AgentContext, result: AgentResult) -> None:
    p = result.pending
    assert p is not None
    context.history.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": p.tool_use_id,
                    "is_error": True,
                    "content": "User cancelled the action.",
                }
            ],
        }
    )
    context.history.append({"role": "assistant", "content": "Azione annullata."})
