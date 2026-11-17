import asyncio
import logging
import mimetypes
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from app import builds, notify
from app.access import access_middleware
from app.advisor import BudgetedLLM, build_advisor_llm, with_advisor_data
from app.agent.llm import RoutedLLM, build_llms
from app.agent.loop import AgentContext
from app.agent.prompts import ADVISOR_STYLE
from app.config import get_settings
from app.conversations import ConversationStore
from app.costs import estimate_cost
from app.db import session as db
from app.events import EventBus
from app.interfaces.telegram import (
    BotState,
    build_application,
    send_confirmation,
    switch_advisor,
)
from app.memory import MemoryStore
from app.radar import approve_to_kit, more_data, run_radar
from app.radar import discard as radar_discard
from app.reminders import reminder_loop
from app.report import generate_report
from app.review import move_to_spec, run_review, weekly_review_loop
from app.scheduler import daily_report_loop
from app.tools.builtin import ADVISOR_ONLY, advisor_registry, default_registry
from app.tracing import Tracer
from app.ui import router as ui_router
from app.voice.realtime import RealtimeSessions, VoiceTurnHandler
from app.voice.routes import build_realtime_router
from app.voice.service import VoiceService
from app.web import router as web_router

mimetypes.add_type("text/javascript", ".js")  # Windows may map .js to text/plain: modules need JS
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
    app.state.events = EventBus()
    tracer = Tracer(db.session_maker())
    llm, fast = build_llms(s)
    full_registry = default_registry()
    registry = full_registry.without(ADVISOR_ONLY)  # everyday chats: a smaller tool list

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

    try:
        advisor_base = build_advisor_llm(s)
    except Exception as e:  # a missing key must not stop Jarvis: the advisor falls back
        log.warning("advisor model not available (%s): using the everyday model", e)
        advisor_base = llm

    async def tell_user(text: str) -> None:  # defined later, resolved when called
        await send_report(text)

    advisor_llm = BudgetedLLM(advisor_base, s.advisor_daily_budget, tell_user)

    def make_advisor_context(product: str) -> AgentContext:
        ctx = AgentContext(
            llm=advisor_llm,
            registry=advisor_registry(product),
            tracer=tracer,
            max_iterations=s.agent_max_iterations + 4,
            tool_timeout=s.tool_timeout_seconds,
            history_limit=60,
            summary_threshold=30,
            keep_recent=12,
        )
        ctx.style = f"{ADVISOR_STYLE}\n\nThe product you advise on: {product}."
        ctx.advisor = True
        ctx.system_extra = with_advisor_data(product, None)  # decisions and brief, always
        return ctx

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
        make_advisor_context=make_advisor_context,
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
            routed = RoutedLLM(fast, llm, registry.names_with("read"))

            async def confirm_on_telegram(result) -> None:
                await send_confirmation(bot.bot, bot_state, chat_id, result)

            handler = VoiceTurnHandler(
                lambda: bot_state.ctx(chat_id), routed, confirm_on_telegram, app.state.events
            )
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

    reminder_task = asyncio.create_task(reminder_loop(send_report))

    owner_chat = next(iter(s.telegram_allowed_user_ids), None)  # the single user's chat
    notify.sender = send_report if owner_chat else None

    async def advisor_switch(product: str | None) -> str:
        return await switch_advisor(bot_state, owner_chat, product)

    notify.advisor_switch = advisor_switch if owner_chat else None

    async def send_review(text: str, moves: list) -> None:
        keyboard = None
        if moves:
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(f"Mossa {i} → bozza", callback_data=f"mv:{m.id}")
                        for i, m in enumerate(moves, 1)
                    ]
                ]
            )
        for user_id in s.telegram_allowed_user_ids:
            await bot.bot.send_message(chat_id=user_id, text=text, reply_markup=keyboard)

    async def do_review() -> None:
        await run_review(make_advisor_context, send_review)

    async def send_radar(text: str, ids: list[int]) -> None:
        keyboard = None
        if ids:
            oid = ids[0]
            keyboard = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("Approva verifica", callback_data=f"op:ok:{oid}"),
                        InlineKeyboardButton("Scarta", callback_data=f"op:no:{oid}"),
                        InlineKeyboardButton("Più dati", callback_data=f"op:more:{oid}"),
                    ]
                ]
            )
        for user_id in s.telegram_allowed_user_ids:
            await bot.bot.send_message(chat_id=user_id, text=text[:4000], reply_markup=keyboard)

    async def do_radar() -> None:
        await run_radar(make_advisor_context, send_radar)

    async def on_opportunity(kind: str, oid: int) -> str:
        if kind == "no":
            await radar_discard(oid)
            return f"Opportunità #{oid} scartata."
        if kind == "more":
            return await more_data(oid, make_advisor_context)
        result, ctx = await approve_to_kit(oid, make_advisor_context)
        if result.pending and owner_chat:
            await send_confirmation(bot.bot, bot_state, owner_chat, result, ctx)
            return "Ecco il kit di verifica: controllalo e conferma qui sopra."
        return result.answer or "(nessuna risposta)"

    bot_state.run_radar = do_radar
    bot_state.on_opportunity = on_opportunity
    radar_task = asyncio.create_task(
        weekly_review_loop(do_radar, s.radar_day, s.radar_time, ZoneInfo(s.report_timezone))
    )
    bot_state.run_review = do_review
    bot_state.on_move = lambda move_id: move_to_spec(move_id, make_advisor_context)
    review_task = asyncio.create_task(
        weekly_review_loop(do_review, s.review_day, s.review_time, ZoneInfo(s.report_timezone))
    )

    async def build_notifier() -> None:
        while True:
            try:
                await builds.notify_changes(send_report)
            except Exception:
                log.exception("build notifier round failed")
            await asyncio.sleep(10)

    build_task = asyncio.create_task(build_notifier())
    report_task = asyncio.create_task(
        daily_report_loop(produce_report, send_report, s.report_time, ZoneInfo(s.report_timezone))
    )
    yield
    report_task.cancel()
    reminder_task.cancel()
    build_task.cancel()
    review_task.cancel()
    radar_task.cancel()
    if realtime is not None:
        await realtime.close()
    await bot.updater.stop()
    await bot.stop()
    await bot.shutdown()
    await http.aclose()
    await db.engine().dispose()


app = FastAPI(title="Jarvis", lifespan=lifespan)
app.middleware("http")(access_middleware(lambda: get_settings().access_token))
app.include_router(web_router)
app.include_router(ui_router)
app.mount("/jarvis/ui", StaticFiles(directory=Path(__file__).parent / "ui"), name="jarvis-ui")


@app.get("/", include_in_schema=False)
async def home() -> RedirectResponse:
    return RedirectResponse("/jarvis")


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
