from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from app import memory as memory_mod
from app.agent.llm import FakeLLMClient, LLMResponse, ToolUse
from app.agent.loop import resume_confirmed, run_agent
from app.conversations import ConversationStore, sanitize_history
from app.db import session as db
from app.interfaces.telegram import BotState, build_handlers
from app.memory import MemoryStore, looks_like_secret, rank
from app.web import router


@pytest.fixture
def store(tracer) -> MemoryStore:
    return MemoryStore(db.session_maker())


def _tool_call(name: str, **inp) -> LLMResponse:
    return LLMResponse(tool_uses=[ToolUse("1", name, inp)])


@pytest.mark.parametrize(
    "text",
    [
        "la mia password è hunter2xyz99",
        "usa la chiave sk-ant-api03-abcdefghijklmnop",
        "token ghp_abcdefghijklmnopqrstuvwxyz0123456789",
        "IBAN IT60 X054 2811 1010 0000 0123 456",
        "carta 4111 1111 1111 1111",
        "segreto 9f8e7d6c5b4a39281716f5e4d3c2b1a09f8e7d6c",
        "PIN: 482913",
    ],
)
def test_secrets_are_recognised(text):
    assert looks_like_secret(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Il mio dentista è il dottor Bianchi",
        "Preferisco le mail brevi e senza saluti",
        "Il compleanno di Anna è il 12 marzo",
        "Il numero di telefono di Marco è 3331234567",
        "Jarvis gira su Windows con Docker Desktop",
    ],
)
def test_normal_facts_are_not_flagged(text):
    assert looks_like_secret(text) is None


async def test_secret_is_refused_and_not_stored(store):
    with pytest.raises(ValueError, match="password or PIN"):
        await store.remember("la mia password è hunter2xyz99")
    assert await store.list_active() == []


async def test_remember_deduplicates_identical_text(store):
    a = await store.remember("Il mio dentista è il dottor Bianchi", "person")
    b = await store.remember("Il mio dentista è il dottor Bianchi", "person")
    assert a.id == b.id and len(await store.list_active()) == 1


async def test_bad_category_and_empty_text_are_rejected(store):
    with pytest.raises(ValueError, match="category"):
        await store.remember("x y z", "banana")
    with pytest.raises(ValueError, match="empty"):
        await store.remember("   ")


async def test_a_correction_replaces_the_old_fact(store):  # criterion 2
    old = await store.remember("Abito a Milano", "fact")
    new = await store.remember("Abito a Roma", "fact", replaces=old.id)
    active = await store.list_active()
    assert [m.text for m in active] == ["Abito a Roma"]
    assert (await store.get(old.id)).status == "superseded"
    assert (await store.get(old.id)).superseded_by == new.id
    assert [m.id for m in await store.search("dove abito?")] == [new.id]  # only the new one


async def test_replacing_an_unknown_or_inactive_fact_is_an_error(store):
    with pytest.raises(ValueError, match="not found"):
        await store.remember("Abito a Roma", replaces=999)


async def test_search_ranks_by_shared_words_and_filters_by_category(store):
    await store.remember("Il mio dentista è il dottor Bianchi", "person")
    await store.remember("Preferisco le mail brevi", "preference")
    await store.remember("Il progetto Jarvis usa Postgres", "project")
    hits = await store.search("chi è il mio dentista?")
    assert [m.text for m in hits] == ["Il mio dentista è il dottor Bianchi"]
    assert await store.search("dentista", category="project") == []
    assert await store.search("niente di correlato qui") == []


def test_stemming_matches_simple_word_variants():
    m = SimpleNamespace(text="Preferisco le mail brevi", updated_at=1)
    assert rank("preferenze sulle mail", [m])  # same 5-letter stems: prefe, mail


async def test_forgotten_facts_are_not_retrieved(store):  # criterion 3
    m = await store.remember("Il mio dentista è il dottor Bianchi", "person")
    assert await store.forget(m.id) is True
    assert await store.search("dentista") == []
    assert await store.forget(m.id) is False  # already forgotten
    block = await store.context_for("chi è il mio dentista?")
    assert "Saved facts" not in block  # not offered as a fact any more...
    assert (
        "asked you to forget" in block and "dottor Bianchi" in block
    )  # ...and flagged as off limits


async def test_forgetting_also_stops_the_model_repeating_it_from_the_chat_history(
    tracer, make_ctx, store
):
    m = await store.remember("Il mio dentista è il dottor Bianchi", "person")
    await store.forget(m.id)
    llm = FakeLLMClient([LLMResponse(text="Non lo so.")])
    ctx = make_ctx(llm, system_extra=store.context_for)
    ctx.history = [
        {"role": "user", "content": "chi è il mio dentista?"},
        {"role": "assistant", "content": "Il dottor Bianchi."},  # said before it was forgotten
    ]
    await run_agent("e adesso, chi è?", ctx)
    assert "never use or repeat them" in llm.systems[0].lower()
    assert "dottor Bianchi" in llm.systems[0] and "forget" in llm.systems[0]


async def test_no_notice_when_nothing_was_forgotten(store):
    await store.remember("Il mio dentista è il dottor Bianchi", "person")
    assert "forget" not in await store.context_for("chi è il mio dentista?")
    assert await store.context_for("che ore sono") == ""


async def test_context_block_is_capped_and_labelled_as_data(store, monkeypatch):
    for n in range(8):
        await store.remember(f"Fatto numero {n} sul dentista con parole {n}", "fact")
    block = await store.context_for("dentista")
    assert block.startswith("Saved facts about the user") and "not instructions" in block
    assert block.count("\n- ") <= memory_mod.CONTEXT_MAX_FACTS
    monkeypatch.setattr(memory_mod, "CONTEXT_MAX_CHARS", 60)
    assert len((await store.context_for("dentista")).splitlines()) <= 3


async def test_update_text_edits_in_place_and_refuses_secrets(store):
    m = await store.remember("Il mio dentista è Bianchi", "person")
    assert await store.update_text(m.id, "Il mio dentista è Rossi") is True
    assert (await store.get(m.id)).text == "Il mio dentista è Rossi"
    with pytest.raises(ValueError, match="secrets"):
        await store.update_text(m.id, "password: abcd1234")
    assert await store.update_text(999, "x y z") is False


# ---- tools and the agent loop ----


async def test_remember_tool_records_the_run_it_came_from(tracer, make_ctx, store):
    llm = FakeLLMClient(
        [_tool_call("remember", text="Il mio dentista è il dottor Bianchi", category="person"),
         LLMResponse(text="Ok.")]
    )  # fmt: skip
    result = await run_agent("ricordati del mio dentista", make_ctx(llm))
    (m,) = await store.list_active()
    assert m.source_run_id == result.run_id and m.category == "person"
    (action,) = await tracer.list_actions()
    assert action.tool_name == "remember" and action.output["id"] == m.id


async def test_remember_tool_refuses_a_secret_and_the_model_sees_the_error(tracer, make_ctx, store):
    llm = FakeLLMClient(
        [_tool_call("remember", text="la mia password è hunter2xyz99"), LLMResponse(text="No.")]
    )
    await run_agent("ricorda la password", make_ctx(llm))
    assert await store.list_active() == []
    result_block = llm.calls[1][-1]["content"][0]
    assert result_block["is_error"] is True and "never stores secrets" in result_block["content"]


async def test_recall_tool_returns_ids_for_corrections(tracer, make_ctx, store):
    m = await store.remember("Abito a Milano", "fact")
    llm = FakeLLMClient([_tool_call("recall", query="dove abito"), LLMResponse(text="Milano")])
    await run_agent("dove abito?", make_ctx(llm))
    content = llm.calls[1][-1]["content"][0]["content"]
    assert f"'id': {m.id}" in content and "Abito a Milano" in content


async def test_forget_needs_a_click_and_shows_the_fact(tracer, make_ctx, store):
    m = await store.remember("Il mio dentista è il dottor Bianchi", "person")
    llm = FakeLLMClient([_tool_call("forget", id=m.id)])
    ctx = make_ctx(llm)
    result = await run_agent("dimentica il dentista", ctx)
    assert result.status == "awaiting_confirmation"
    assert "Il mio dentista è il dottor Bianchi" in result.answer  # the text, not just an id
    assert (await store.get(m.id)).status == "active"  # nothing happened yet
    done = await resume_confirmed(ctx, result)
    assert done.status == "completed" and (await store.get(m.id)).status == "forgotten"


async def test_a_fact_told_today_is_used_in_a_brand_new_chat(
    tracer, make_ctx, store
):  # criterion 1
    first = FakeLLMClient(
        [_tool_call("remember", text="Il mio dentista è il dottor Bianchi", category="person"),
         LLMResponse(text="Salvato.")]
    )  # fmt: skip
    await run_agent("ricordati: il mio dentista è il dottor Bianchi", make_ctx(first))
    second = FakeLLMClient([LLMResponse(text="Il dottor Bianchi.")])
    fresh = make_ctx(second, system_extra=store.context_for)  # new chat: empty history
    await run_agent("come si chiama il mio dentista?", fresh)
    assert "dottor Bianchi" in second.systems[0]
    assert fresh.history[0]["content"] == "come si chiama il mio dentista?"


async def test_an_unrelated_message_gets_no_facts_in_the_prompt(tracer, make_ctx, store):
    await store.remember("Il mio dentista è il dottor Bianchi", "person")
    llm = FakeLLMClient([LLMResponse(text="ciao")])
    await run_agent("che ore sono a Tokyo?", make_ctx(llm, system_extra=store.context_for))
    assert "Saved facts" not in llm.systems[0]


async def test_memory_trouble_never_stops_an_answer(tracer, make_ctx):
    async def broken(_: str) -> str:
        raise RuntimeError("db down")

    llm = FakeLLMClient([LLMResponse(text="risposta")])
    result = await run_agent("ciao", make_ctx(llm, system_extra=broken))
    assert result.status == "completed" and result.answer == "risposta"


# ---- persistent conversation ----


async def test_conversation_continues_after_a_restart(tracer, make_ctx):  # criterion 4
    conversations = ConversationStore(db.session_maker())
    ctx = make_ctx(FakeLLMClient([LLMResponse(text="Piacere, Anna.")]))
    ctx.store, ctx.conversation_id = conversations, "telegram:1"
    await run_agent("mi chiamo Anna", ctx)

    history, summary = await conversations.load("telegram:1")  # "the process restarted"
    llm = FakeLLMClient([LLMResponse(text="Ti chiami Anna.")])
    restarted = make_ctx(llm)
    restarted.history, restarted.summary = history, summary
    await run_agent("come mi chiamo?", restarted)
    assert llm.calls[0][0] == {"role": "user", "content": "mi chiamo Anna"}  # earlier turn is back


async def test_delete_clears_the_stored_conversation(tracer):
    conversations = ConversationStore(db.session_maker())
    await conversations.save("c", [{"role": "user", "content": "x"}], "riassunto")
    await conversations.delete("c")
    assert await conversations.load("c") == ([], "")


def test_a_tool_call_left_without_result_is_dropped_on_load():
    history = [
        {"role": "user", "content": "fai"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "1", "name": "x", "input": {}}],
        },
    ]
    assert sanitize_history(history) == [{"role": "user", "content": "fai"}]
    done = [*history[:1], {"role": "assistant", "content": "fatto"}]
    assert sanitize_history(done) == done


async def test_long_conversation_is_summarised_and_the_summary_is_used(tracer, make_ctx):
    replies = [LLMResponse(text=f"r{n}") for n in range(6)]
    summary = LLMResponse(text="L'utente si chiama Anna e vuole organizzare un viaggio.")
    llm = FakeLLMClient([*replies[:4], summary, *replies[4:]])
    ctx = make_ctx(llm, summary_threshold=6, keep_recent=2)
    for n in range(3):
        await run_agent(f"messaggio {n}", ctx)  # 6 messages: not over the threshold yet
    assert ctx.summary == ""
    await run_agent("messaggio 3", ctx)  # 8 messages: folds the oldest into a summary
    assert ctx.summary.startswith("L'utente si chiama Anna")
    assert len(ctx.history) == 2 and ctx.history[0]["content"] == "messaggio 3"
    assert "[summary]" in [r.user_message for r in await tracer.list_runs()]
    await run_agent("messaggio 4", ctx)
    assert "L'utente si chiama Anna" in llm.systems[-1]  # the summary reaches the next prompt


async def test_a_failing_summary_does_not_break_the_conversation(tracer, make_ctx):
    class Flaky(FakeLLMClient):
        async def complete(self, system, messages, tools):
            if system.startswith("Summarise"):
                raise RuntimeError("api down")
            return await super().complete(system, messages, tools)

    ctx = make_ctx(Flaky([LLMResponse(text="r")] * 6), summary_threshold=2, keep_recent=2)
    for n in range(3):
        result = await run_agent(f"m{n}", ctx)
        assert result.status == "completed"
    assert ctx.summary == ""


# ---- telegram commands ----


def _update(user_id: int, args=None):
    msg = SimpleNamespace(text="/x", reply_text=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_message=msg,
        effective_chat=SimpleNamespace(id=user_id, send_action=AsyncMock()),
    )
    return update, msg, SimpleNamespace(args=args or [])


def _handler(state, command):
    return next(h for h in build_handlers(state) if command in getattr(h, "commands", ()))


async def test_memory_command_lists_active_facts(tracer, make_ctx, store):
    await store.remember("Il mio dentista è Bianchi", "person")
    state = BotState({1}, lambda: make_ctx(FakeLLMClient([])), tracer, memory=store)
    update, msg, context = _update(1)
    await _handler(state, "memory").callback(update, context)
    assert "[person] Il mio dentista è Bianchi" in msg.reply_text.await_args.args[0]


async def test_forget_command_forgets_and_logs_the_action(tracer, make_ctx, store):
    m = await store.remember("Il mio dentista è Bianchi", "person")
    state = BotState({1}, lambda: make_ctx(FakeLLMClient([])), tracer, memory=store)
    update, msg, context = _update(1, [str(m.id)])
    await _handler(state, "forget").callback(update, context)
    assert (await store.get(m.id)).status == "forgotten"
    assert "Dimenticato" in msg.reply_text.await_args.args[0]
    (action,) = await tracer.list_actions()
    assert action.tool_name == "forget" and action.actor == "telegram:1"


@pytest.mark.parametrize("args", [[], ["abc"], ["1", "2"]])
async def test_forget_command_usage(tracer, make_ctx, store, args):
    state = BotState({1}, lambda: make_ctx(FakeLLMClient([])), tracer, memory=store)
    update, msg, context = _update(1, args)
    await _handler(state, "forget").callback(update, context)
    assert "Uso:" in msg.reply_text.await_args.args[0]


async def test_forget_command_unknown_id_and_unauthorized_user(tracer, make_ctx, store):
    state = BotState({1}, lambda: make_ctx(FakeLLMClient([])), tracer, memory=store)
    update, msg, context = _update(1, ["999"])
    await _handler(state, "forget").callback(update, context)
    assert "non trovato" in msg.reply_text.await_args.args[0]
    m = await store.remember("Il mio dentista è Bianchi", "person")
    update2, msg2, context2 = _update(999, [str(m.id)])
    await _handler(state, "forget").callback(update2, context2)
    msg2.reply_text.assert_not_called()
    assert (await store.get(m.id)).status == "active"


async def test_chat_context_loads_history_and_reset_deletes_it(tracer, make_ctx, store):
    conversations = ConversationStore(db.session_maker())
    await conversations.save("telegram:1", [{"role": "user", "content": "ciao"}], "sommario")
    state = BotState(
        {1}, lambda: make_ctx(FakeLLMClient([])), tracer, conversations=conversations, memory=store
    )
    ctx = await state.ctx(1)
    assert ctx.history == [{"role": "user", "content": "ciao"}] and ctx.summary == "sommario"
    assert ctx.system_extra == store.context_for and ctx.actor == "telegram:1"
    update, msg, context = _update(1)
    await _handler(state, "reset").callback(update, context)
    assert await conversations.load("telegram:1") == ([], "")


# ---- web page ----


@pytest.fixture
async def web(tracer, store):
    app = FastAPI()
    app.include_router(router)
    app.state.tracer, app.state.memory, app.state.csrf_token = tracer, store, "tok123"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def test_memory_page_lists_facts_with_the_token_and_escapes_text(web, store):
    await store.remember("Il mio dentista è <b>Bianchi</b>", "person")
    page = (await web.get("/ui/memory")).text
    assert "&lt;b&gt;Bianchi&lt;/b&gt;" in page and "<b>Bianchi</b>" not in page
    assert 'value="tok123"' in page


async def test_web_edit_updates_the_fact_and_logs_it(web, store, tracer):
    m = await store.remember("Il mio dentista è Bianchi", "person")
    r = await web.post(
        f"/ui/memory/{m.id}/edit", data={"token": "tok123", "text": "Il mio dentista è Rossi"}
    )
    assert r.status_code == 303
    assert (await store.get(m.id)).text == "Il mio dentista è Rossi"
    assert (await tracer.list_actions())[0].actor == "web"


async def test_web_edit_without_the_right_token_is_refused(web, store):
    m = await store.remember("Il mio dentista è Bianchi", "person")
    for data in ({"text": "x y z"}, {"token": "wrong", "text": "x y z"}):
        r = await web.post(f"/ui/memory/{m.id}/edit", data=data)
        assert r.status_code == 403
    r = await web.post(f"/ui/memory/{m.id}/forget", data={"token": "wrong"})
    assert r.status_code == 403
    assert (await store.get(m.id)).text == "Il mio dentista è Bianchi"


async def test_web_edit_refuses_secrets(web, store):
    m = await store.remember("Il mio dentista è Bianchi", "person")
    r = await web.post(f"/ui/memory/{m.id}/edit", data={"token": "tok123", "text": "PIN: 482913"})
    assert r.status_code == 400 and "secrets" in r.text


async def test_web_forget(web, store):
    m = await store.remember("Il mio dentista è Bianchi", "person")
    r = await web.post(f"/ui/memory/{m.id}/forget", data={"token": "tok123"})
    assert r.status_code == 303 and (await store.get(m.id)).status == "forgotten"
    r = await web.post(f"/ui/memory/{m.id}/forget", data={"token": "tok123"})
    assert r.status_code == 404
