import asyncio
import logging
import secrets
from contextlib import asynccontextmanager
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import text

from app.agent.llm import AnthropicClient, RoutedLLM
from app.agent.loop import AgentContext
from app.config import get_settings
from app.conversations import ConversationStore
from app.costs import estimate_cost
from app.db import session as db
from app.interfaces.telegram import BotState, build_application, send_confirmation
from app.memory import MemoryStore
from app.report import generate_report
from app.scheduler import daily_report_loop
from app.tools.builtin import default_registry
from app.tracing import Tracer
from app.voice.realtime import RealtimeSessions, VoiceTurnHandler
from app.voice.routes import build_realtime_router
from app.voice.service import VoiceService
from app.web import router as web_router

log = logging.getLogger("jarvis.main")


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
        "cost_usd": estimate_cost(run.model, run.input_tokens, run.output_tokens),
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

    memory = MemoryStore(db.session_maker())
    conversations = ConversationStore(db.session_maker())

    def make_context() -> AgentContext:
        return AgentContext(
            llm=llm,
            registry=registry,
            tracer=tracer,
            max_iterations=s.agent_max_iterations,
            tool_timeout=s.tool_timeout_seconds,
            history_limit=60,  # hard cap; older turns are folded into a summary before this
            summary_threshold=30,
            keep_recent=12,
        )

    http = httpx.AsyncClient(timeout=60)  # shared by the speech providers
    voice: VoiceService | None = None
    try:
        voice = VoiceService(s, http)
    except ValueError as e:  # text keeps working; voice messages are simply not handled
        log.warning("voice disabled: %s", e)

    app.state.tracer = tracer
    app.state.memory = memory
    app.state.csrf_token = secrets.token_urlsafe(32)  # guards the web page's edit/forget forms
    bot_state = BotState(
        set(s.telegram_allowed_user_ids),
        make_context,
        tracer,
        conversations=conversations,
        memory=memory,
        voice=voice,
    )
    bot = build_application(s.telegram_bot_token, bot_state)
    await bot.initialize()
    await bot.start()
    await bot.updater.start_polling()

    realtime: RealtimeSessions | None = None
    if s.realtime_voice:
        if voice is None or not s.telegram_allowed_user_ids:
            log.warning("real-time voice disabled: needs working voice keys and an allowed user")
        else:
            chat_id = s.telegram_allowed_user_ids[0]  # the voice page shares this Telegram chat
            fast = AnthropicClient(s.anthropic_api_key, s.fast_model, "low")
            routed = RoutedLLM(fast, llm, registry.names_with("read"))

            async def confirm_on_telegram(result) -> None:
                await send_confirmation(bot.bot, bot_state, chat_id, result)

            handler = VoiceTurnHandler(lambda: bot_state.ctx(chat_id), routed, confirm_on_telegram)
            realtime = RealtimeSessions(s, handler)
            app.state.realtime = realtime
            from pipecat_ai_prebuilt.frontend import PipecatPrebuiltUI

            app.include_router(build_realtime_router())
            app.mount("/client", PipecatPrebuiltUI)
            log.info("real-time voice on: open http://localhost:8000/client/")

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
    if realtime is not None:
        await realtime.close()
    await bot.updater.stop()
    await bot.stop()
    await bot.shutdown()
    await http.aclose()
    await db.engine().dispose()


app = FastAPI(title="Jarvis", lifespan=lifespan)
app.include_router(web_router)


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
