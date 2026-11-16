import asyncio
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.agent.llm import AnthropicClient
from app.agent.loop import AgentContext
from app.config import get_settings
from app.db import session as db
from app.interfaces.telegram import BotState, build_application
from app.report import generate_report
from app.scheduler import daily_report_loop
from app.tools.builtin import default_registry
from app.tracing import Tracer


def _run_dict(run, with_steps: bool = False) -> dict:
    d = {
        "id": run.id,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "status": run.status,
        "user_message": run.user_message,
        "final_answer": run.final_answer,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "model": run.model,
    }
    if with_steps:
        d["steps"] = [
            {
                "idx": s.idx,
                "type": s.type,
                "tool_name": s.tool_name,
                "input": s.input,
                "output": s.output,
                "error": s.error,
                "duration_ms": s.duration_ms,
                "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens,
            }
            for s in run.steps
        ]
    return d


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()  # fails fast with a clear pydantic error if a variable is missing
    db.init_db(s.database_url)
    tracer = Tracer(db.session_maker())
    llm = AnthropicClient(s.anthropic_api_key, s.anthropic_model)
    registry = default_registry()

    def make_context() -> AgentContext:
        return AgentContext(
            llm=llm,
            registry=registry,
            tracer=tracer,
            max_iterations=s.agent_max_iterations,
            tool_timeout=s.tool_timeout_seconds,
        )

    app.state.tracer = tracer
    bot = build_application(
        s.telegram_bot_token, BotState(set(s.telegram_allowed_user_ids), make_context, tracer)
    )
    await bot.initialize()
    await bot.start()
    await bot.updater.start_polling()

    async def send_report(text: str) -> None:
        for user_id in s.telegram_allowed_user_ids:
            await bot.bot.send_message(chat_id=user_id, text=text)

    async def produce_report() -> str:
        result = await generate_report(make_context())
        if result.status != "completed":
            raise RuntimeError(f"run {result.run_id} ended with status {result.status}")
        return result.answer

    report_task = asyncio.create_task(
        daily_report_loop(produce_report, send_report, s.report_time, ZoneInfo(s.report_timezone))
    )
    yield
    report_task.cancel()
    await bot.updater.stop()
    await bot.stop()
    await bot.shutdown()
    await db.engine().dispose()


app = FastAPI(title="Jarvis", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    try:
        async with db.engine().connect() as c:
            await c.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}
    except Exception:
        return {"status": "degraded", "database": "error"}


@app.get("/runs")
async def runs(limit: int = 20) -> list[dict]:
    return [_run_dict(r) for r in await app.state.tracer.list_runs(min(limit, 200))]


@app.get("/runs/{run_id}")
async def run_detail(run_id: int) -> dict:
    run = await app.state.tracer.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return _run_dict(run, with_steps=True)
