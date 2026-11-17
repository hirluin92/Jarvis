import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

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

from app import advisor as advisor_mod
from app import builds
from app.actions_view import format_action
from app.agent.loop import (
    AgentContext,
    AgentResult,
    cancel_pending,
    resume_confirmed,
    run_agent,
)
from app.agent.prompts import VOICE_STYLE
from app.conversations import ConversationStore
from app.costs import estimate_cost, format_cost
from app.feedback import mark_bad_and_draft
from app.memory import MemoryStore, format_memory
from app.report import generate_report
from app.tracing import Tracer
from app.voice.providers import Audio
from app.voice.service import VoiceService, speakable

log = logging.getLogger("jarvis.telegram")


@dataclass
class BotState:
    allowed_ids: set[int]
    make_context: callable  # () -> AgentContext
    tracer: Tracer
    contexts: dict[int, AgentContext] = field(default_factory=dict)
    pending: dict[int, AgentResult] = field(default_factory=dict)
    last_run: dict[int, int] = field(default_factory=dict)
    conversations: ConversationStore | None = None  # persistent history (Postgres)
    memory: MemoryStore | None = None  # saved facts; also feeds the context of each run
    voice: VoiceService | None = None  # speech in/out; None = voice messages are ignored
    # advisor mode (phase 11): a chat can talk to a separate, more capable model about a product
    make_advisor_context: Any = None  # (product) -> AgentContext
    advisor: dict[int, str] = field(default_factory=dict)  # chat id -> product being advised on
    advisor_contexts: dict[tuple[int, str], AgentContext] = field(default_factory=dict)
    pending_ctx: dict[int, AgentContext] = field(default_factory=dict)  # who made a pending action
    run_review: Any = None  # () -> None: produce and send the weekly review now
    on_move: Any = None  # (move_id) -> str: turn a review move into a draft spec

    async def ctx(self, chat_id: int) -> AgentContext:
        product = self.advisor.get(chat_id)
        if product and self.make_advisor_context is not None:
            key = (chat_id, product)
            if key not in self.advisor_contexts:
                actx = self.make_advisor_context(product)
                actx.actor = f"telegram:{chat_id}"
                if self.memory is not None:
                    actx.system_extra = self.memory.context_for
                if self.conversations is not None:
                    actx.store = self.conversations
                    actx.conversation_id = f"advisor:{chat_id}:{product.lower()}"
                    actx.history, actx.summary = await self.conversations.load(actx.conversation_id)
                self.advisor_contexts[key] = actx
            return self.advisor_contexts[key]
        if chat_id not in self.contexts:
            ctx = self.make_context()
            ctx.actor = f"telegram:{chat_id}"
            if self.memory is not None:
                ctx.system_extra = self.memory.context_for
            if self.conversations is not None:
                ctx.store, ctx.conversation_id = self.conversations, f"telegram:{chat_id}"
                ctx.history, ctx.summary = await self.conversations.load(ctx.conversation_id)
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


def _confirmation_keyboard(result: AgentResult) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Conferma", callback_data=f"ok:{result.run_id}"),
                InlineKeyboardButton("Annulla", callback_data=f"no:{result.run_id}"),
            ]
        ]
    )


async def switch_advisor(state: BotState, chat_id: int, product: str | None) -> str:
    """Enter advisor mode on a product, or leave it (None). Returns what to tell the user."""
    if product is None:
        had = state.advisor.pop(chat_id, None)
        return "Modalità consigliere chiusa." if had else "Non eri in modalità consigliere."
    if state.make_advisor_context is None:
        return "La modalità consigliere non è disponibile."
    try:
        name = advisor_mod.canonical(product)
    except ValueError as e:
        return str(e)
    state.advisor[chat_id] = name
    return (
        f"Modalità consigliere su {name}. Parlami del prodotto; /fine per chiudere con un "
        "riepilogo, /esci per tornare normale."
    )


async def send_confirmation(bot, state: BotState, chat_id: int, result: AgentResult) -> None:
    """Show a pending confirmation in the Telegram chat, with its buttons. Used when the request
    came from somewhere without buttons (the voice page): only a click here can approve it."""
    state.pending[chat_id] = result
    state.pending_ctx[chat_id] = await state.ctx(chat_id)
    state.last_run[chat_id] = result.run_id
    await bot.send_message(
        chat_id=chat_id, text=result.answer, reply_markup=_confirmation_keyboard(result)
    )


async def _reply(update: Update, state: BotState, result: AgentResult) -> None:
    chat_id = update.effective_chat.id
    state.last_run[chat_id] = result.run_id
    if result.pending:
        state.pending[chat_id] = result
        await update.effective_message.reply_text(
            result.answer, reply_markup=_confirmation_keyboard(result)
        )
    else:
        await update.effective_message.reply_text(result.answer or "(nessuna risposta)")


async def _speak(update: Update, voice: VoiceService, answer: str) -> None:
    """Send the answer as a voice message. The text was already sent, so a failure is harmless."""
    try:
        said = await voice.speak(speakable(answer))
        await update.effective_message.reply_voice(voice=said.audio.data)
    except Exception:
        log.exception("could not send the spoken answer")


def build_handlers(state: BotState) -> list:
    async def start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if _allowed(state, update):
            await update.effective_message.reply_text("Ciao, sono Jarvis. Come posso aiutarti?")

    async def reset(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if _allowed(state, update):
            cid = update.effective_chat.id
            state.contexts.pop(cid, None)
            state.pending.pop(cid, None)
            if state.conversations is not None:
                await state.conversations.delete(f"telegram:{cid}")
            await update.effective_message.reply_text("Cronologia svuotata.")

    async def memory_list(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update) or state.memory is None:
            return
        facts = await state.memory.list_active()
        text = "\n".join(format_memory(m) for m in facts) or "Nessun fatto salvato."
        await update.effective_message.reply_text(text[:4000])

    async def forget_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update) or state.memory is None:
            return
        args = getattr(context, "args", None) or []
        if len(args) != 1 or not args[0].isdigit():
            await update.effective_message.reply_text("Uso: /forget <id> (gli id sono in /memory)")
            return
        memory_id = int(args[0])
        fact = await state.memory.get(memory_id)
        # the user typed the command themselves: that is the explicit consent, no extra click
        if fact is None or not await state.memory.forget(memory_id):
            await update.effective_message.reply_text(f"Fatto #{memory_id} non trovato.")
            return
        await state.tracer.record_action(
            f"telegram:{update.effective_chat.id}",
            None,
            "forget",
            "command",
            {"id": memory_id, "text": fact.text},
            {"id": memory_id, "status": "forgotten"},
            None,
        )
        await update.effective_message.reply_text(f"Dimenticato #{memory_id}: «{fact.text}»")

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

    async def builds_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update):
            return
        jobs = await builds.list_jobs(10)
        text = "\n".join(builds.format_job(j) for j in jobs) or "Nessun lavoro."
        await update.effective_message.reply_text(text[:4000])

    async def build_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update):
            return
        args = getattr(context, "args", None) or []
        if len(args) != 1 or not args[0].isdigit():
            await update.effective_message.reply_text("Uso: /build <id> (gli id sono in /builds)")
            return
        try:
            job = await builds.get_job(int(args[0]))
        except ValueError as e:
            await update.effective_message.reply_text(str(e))
            return
        lines = [builds.format_job(job), f"durata: {job.duration_s or 0} s"]
        if job.log_summary:
            lines.append(job.log_summary)
        await update.effective_message.reply_text("\n\n".join(lines)[:4000])

    async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update):
            return
        args = getattr(context, "args", None) or []
        if len(args) != 1 or not args[0].isdigit():
            await update.effective_message.reply_text("Uso: /resume <id> (lavoro in pausa)")
            return
        try:  # the user typed the command: that is the consent to continue
            job = await builds.resume(int(args[0]))
        except ValueError as e:
            await update.effective_message.reply_text(str(e))
            return
        await update.effective_message.reply_text(f"Lavoro #{job.id} rimesso in coda.")

    async def consiglio_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update):
            return
        args = getattr(context, "args", None) or []
        if not args:
            await update.effective_message.reply_text("Uso: /consiglio <prodotto>")
            return
        msg = await switch_advisor(state, update.effective_chat.id, " ".join(args))
        await update.effective_message.reply_text(msg)

    async def esci_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if _allowed(state, update):
            msg = await switch_advisor(state, update.effective_chat.id, None)
            await update.effective_message.reply_text(msg)

    async def fine_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update):
            return
        if update.effective_chat.id not in state.advisor:
            await update.effective_message.reply_text("Non sei in modalità consigliere.")
            return
        await answer(
            update,
            "Chiudiamo la sessione. Prepara il riepilogo con le decisioni e le azioni e proponimi "
            "di salvarlo con save_session_summary.",
            spoken=False,
        )

    async def review_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update) or state.run_review is None:
            return
        await update.effective_message.reply_text("Preparo il riesame, ci vuole qualche minuto…")
        await state.run_review()

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

    async def answer(update: Update, text: str, spoken: bool) -> None:
        """One path for typed and spoken requests: same agent, same confirmations."""
        cid = update.effective_chat.id
        ctx = await state.ctx(cid)
        if old := state.pending.pop(cid, None):
            owner = state.pending_ctx.pop(cid, None) or ctx
            cancel_pending(owner, old)  # keep history valid: unanswered confirm = cancelled
        typing = asyncio.create_task(_typing(update))
        try:
            result = await run_agent(text, ctx, style=VOICE_STYLE if spoken else "")
        finally:
            typing.cancel()
        if result.pending:
            state.pending_ctx[cid] = ctx
        await _reply(update, state, result)
        # a pending confirmation is shown on screen with its buttons, never settled by voice
        if spoken and state.voice is not None and not result.pending and result.answer:
            await _speak(update, state.voice, result.answer)

    async def on_text(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update) or not update.effective_message.text:
            return
        await answer(update, update.effective_message.text, spoken=False)

    async def on_voice(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        if not _allowed(state, update) or state.voice is None:  # check before any download
            return
        msg = update.effective_message
        try:
            tg_file = await msg.voice.get_file()
            data = bytes(await tg_file.download_as_bytearray())
            heard = await state.voice.transcribe(Audio(data, "audio/ogg", "ogg"))
        except Exception:
            log.exception("could not transcribe a voice message")
            await msg.reply_text("Non sono riuscito a trascrivere il vocale: riprova o scrivimi.")
            return
        text = heard.text.strip()
        if not text:
            await msg.reply_text("Non ho capito: puoi ripetere?")
            return
        await msg.reply_text(f"🎤 «{text}»")  # show what was understood, so errors are visible
        await answer(update, text, spoken=True)

    async def on_button(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if q.from_user.id not in state.allowed_ids:
            log.warning("ignored callback from unauthorized user id=%s", q.from_user.id)
            return
        await q.answer()
        cid = update.effective_chat.id
        action, _, run_id = (q.data or "").partition(":")
        if action == "mv":  # a move of the weekly review becomes a draft spec
            if state.on_move is None or not run_id.isdigit():
                await q.edit_message_text("Mossa non disponibile.")
                return
            await q.edit_message_text("Scrivo la bozza di specifica…")
            await q.message.reply_text(await state.on_move(int(run_id)))
            return
        pending = state.pending.get(cid)
        if pending is None or str(pending.run_id) != run_id:
            await q.edit_message_text("Richiesta scaduta o già usata.")
            return
        state.pending.pop(cid)  # single use: a second click finds nothing
        ctx = state.pending_ctx.pop(cid, None) or await state.ctx(cid)
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
        CommandHandler("builds", builds_cmd),
        CommandHandler("build", build_cmd),
        CommandHandler("resume", resume_cmd),
        CommandHandler("consiglio", consiglio_cmd),
        CommandHandler("esci", esci_cmd),
        CommandHandler("fine", fine_cmd),
        CommandHandler("riesame", review_cmd),
        CommandHandler("memory", memory_list),
        CommandHandler("forget", forget_cmd),
        CallbackQueryHandler(on_button),
        MessageHandler(filters.VOICE, on_voice),
        MessageHandler(filters.TEXT & ~filters.COMMAND, on_text),
    ]


def build_application(token: str, state: BotState) -> Application:
    app = Application.builder().token(token).build()
    for h in build_handlers(state):
        app.add_handler(h)
    return app
