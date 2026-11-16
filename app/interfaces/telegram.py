import asyncio
import logging
from dataclasses import dataclass, field

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.actions_view import format_action
from app.agent.loop import (
    AgentContext,
    AgentResult,
    cancel_pending,
    resume_confirmed,
    run_agent,
)
from app.costs import estimate_cost, format_cost
from app.feedback import mark_bad_and_draft
from app.report import generate_report
from app.tracing import Tracer

log = logging.getLogger("jarvis.telegram")


@dataclass
class BotState:
    allowed_ids: set[int]
    make_context: callable  # () -> AgentContext
    tracer: Tracer
    contexts: dict[int, AgentContext] = field(default_factory=dict)
    pending: dict[int, AgentResult] = field(default_factory=dict)
    last_run: dict[int, int] = field(default_factory=dict)

    def ctx(self, chat_id: int) -> AgentContext:
        if chat_id not in self.contexts:
            ctx = self.make_context()
            ctx.actor = f"telegram:{chat_id}"
            self.contexts[chat_id] = ctx
        return self.contexts[chat_id]


def _allowed(state: BotState, update: Update) -> bool:
    user = update.effective_user
    if user is None or user.id not in state.allowed_ids:
        log.warning("ignored message from unauthorized user id=%s", user.id if user else None)
        return False
    return True


async def _typing(update: Update) -> None:
    while True:
        await update.effective_chat.send_action(ChatAction.TYPING)
        await asyncio.sleep(4)


async def _reply(update: Update, state: BotState, result: AgentResult) -> None:
    chat_id = update.effective_chat.id
    state.last_run[chat_id] = result.run_id
    if result.pending:
        state.pending[chat_id] = result
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Conferma", callback_data=f"ok:{result.run_id}"),
                    InlineKeyboardButton("Annulla", callback_data=f"no:{result.run_id}"),
                ]
            ]
        )
        await update.effective_message.reply_text(result.answer, reply_markup=kb)
    else:
        await update.effective_message.reply_text(result.answer or "(nessuna risposta)")


def build_handlers(state: BotState) -> list:
    async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if _allowed(state, update):
            await update.effective_message.reply_text("Ciao, sono Jarvis. Come posso aiutarti?")

    async def reset(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if _allowed(state, update):
            cid = update.effective_chat.id
            state.contexts.pop(cid, None)
            state.pending.pop(cid, None)
            await update.effective_message.reply_text("Cronologia svuotata.")

    async def last(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update):
            return
        run_id = state.last_run.get(update.effective_chat.id)
        run = await state.tracer.get_run(run_id) if run_id else None
        if run is None:
            await update.effective_message.reply_text("Nessun run recente.")
            return
        tools = [s.tool_name for s in run.steps if s.type == "tool_call"]
        dur = sum(s.duration_ms for s in run.steps)
        cost = format_cost(estimate_cost(run.model, run.input_tokens, run.output_tokens))
        await update.effective_message.reply_text(
            f"Run {run.id} [{run.status}]\nTool: {', '.join(tools) or 'nessuno'}\n"
            f"Durata: {dur} ms\nToken: {run.input_tokens} in / {run.output_tokens} out\n"
            f"Costo stimato: {cost}"
        )

    async def actions(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update):
            return
        rows = await state.tracer.list_actions(10)
        text = "\n".join(format_action(a) for a in rows) or "Nessuna azione registrata."
        await update.effective_message.reply_text(text)

    async def bad(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update):
            return
        run_id = state.last_run.get(update.effective_chat.id)
        path = await mark_bad_and_draft(state.tracer, run_id) if run_id else None
        if path is None:
            await update.effective_message.reply_text("Nessun run recente da segnalare.")
            return
        await update.effective_message.reply_text(
            f"Run {run_id} segnalato come sbagliato. Bozza di scenario: evals/scenarios/drafts/"
            f"{path.name}"
        )

    async def report(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update):
            return
        typing = asyncio.create_task(_typing(update))
        try:
            result = await generate_report(state.make_context())
        finally:
            typing.cancel()
        await _reply(update, state, result)

    async def on_text(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update) or not update.effective_message.text:
            return
        cid = update.effective_chat.id
        ctx = state.ctx(cid)
        if old := state.pending.pop(cid, None):
            cancel_pending(ctx, old)  # keep history valid: unanswered confirm = cancelled
        typing = asyncio.create_task(_typing(update))
        try:
            result = await run_agent(update.effective_message.text, ctx)
        finally:
            typing.cancel()
        await _reply(update, state, result)

    async def on_button(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if q.from_user.id not in state.allowed_ids:
            log.warning("ignored callback from unauthorized user id=%s", q.from_user.id)
            return
        await q.answer()
        cid = update.effective_chat.id
        action, _, run_id = (q.data or "").partition(":")
        pending = state.pending.get(cid)
        if pending is None or str(pending.run_id) != run_id:
            await q.edit_message_text("Richiesta scaduta o già usata.")
            return
        state.pending.pop(cid)  # single use: a second click finds nothing
        ctx = state.ctx(cid)
        if pending.pending is not None and pending.pending.expired():
            cancel_pending(ctx, pending)
            await q.edit_message_text("Richiesta scaduta (oltre 10 minuti): azione non eseguita.")
            return
        if action == "ok":
            res = await resume_confirmed(ctx, pending)
            await q.edit_message_text(res.answer)
        else:
            cancel_pending(ctx, pending)
            await q.edit_message_text("Azione annullata.")

    return [
        CommandHandler("start", start),
        CommandHandler("reset", reset),
        CommandHandler("last", last),
        CommandHandler("report", report),
        CommandHandler("bad", bad),
        CommandHandler("actions", actions),
        CallbackQueryHandler(on_button),
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text),
    ]


def build_application(token: str, state: BotState) -> Application:
    app = Application.builder().token(token).build()
    for h in build_handlers(state):
        app.add_handler(h)
    return app
