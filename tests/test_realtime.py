import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from app.agent.llm import FakeLLMClient, LLMResponse, RoutedLLM, ToolUse
from app.agent.loop import AgentResult, PendingAction, run_agent
from app.agent.prompts import VOICE_STYLE
from app.interfaces.telegram import BotState, send_confirmation
from app.tools.registry import Tool, ToolRegistry
from app.voice.realtime import (
    LATENCY_STEP_IDX,
    PENDING_SPEECH,
    Sentencer,
    TurnClock,
    VoiceTurnHandler,
    turn_text,
)
from app.voice.routes import build_realtime_router


def _frame(messages, speculation=False):
    return SimpleNamespace(context=SimpleNamespace(messages=messages), speculation=speculation)


# ---- turn text and clock ----


def test_turn_text_is_the_latest_user_message():
    messages = [
        {"role": "user", "content": "primo"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "  che ore sono?  "},
    ]
    assert turn_text(_frame(messages)) == "che ore sono?"


def test_turn_text_reads_text_blocks_and_ignores_the_rest():
    blocks = [{"type": "text", "text": "ciao"}, {"type": "text", "text": "Jarvis"}]
    assert turn_text(_frame([{"role": "user", "content": blocks}])) == "ciao Jarvis"
    assert turn_text(_frame([{"role": "assistant", "content": "x"}])) is None
    assert turn_text(_frame([{"role": "user", "content": "   "}])) is None
    assert turn_text(_frame([])) is None


def test_a_speculative_turn_is_never_answered():
    # provisional turns can be withdrawn: answering would speak over the user and run tools
    assert turn_text(_frame([{"role": "user", "content": "ciao"}], speculation=True)) is None


def test_clock_breaks_the_wait_into_stages():
    c = TurnClock(speech_end=10.0, heard=10.4, first_text=10.9, answered=11.6, first_audio=11.1)
    assert c.as_dict() == {
        "transcript_s": 0.4, "agent_s": 0.5, "voice_s": 0.2, "total_s": 1.1, "reply_done_s": 1.2,
    }  # fmt: skip
    assert TurnClock(speech_end=1.0).as_dict()["total_s"] is None  # unfinished turn


# ---- cutting the streamed reply into sentences ----


async def _feed(pieces, stale=lambda: False, tail=True):
    said = []

    async def say(s):
        said.append(s)

    sentencer = Sentencer(say, stale)
    for piece in pieces:
        await sentencer.feed(piece)
    if tail:
        await sentencer.flush()
    return said, sentencer


async def test_a_sentence_is_handed_over_as_soon_as_it_is_complete():
    said, _ = await _feed(["Sono le sette ", "e quarantuno. ", "Buona ", "serata."], tail=False)
    assert said == ["Sono le sette e quarantuno."]  # the rest is still being written


async def test_the_end_of_reply_marker_releases_the_last_sentence_at_once():
    # the model client ends each reply with a line break: the last sentence need not wait for
    # the run (tools, trace, summary) to finish before it is spoken
    said, _ = await _feed(["Mi chiamo Jarvis, sono il tuo assistente.", "\n"], tail=False)
    assert said == ["Mi chiamo Jarvis, sono il tuo assistente."]


async def test_the_unfinished_tail_is_spoken_at_the_end():
    said, s = await _feed(["Prima frase completa. ", "E poi il finale"])
    assert said == ["Prima frase completa.", "E poi il finale"] and s.spoken


async def test_numbers_and_short_fragments_do_not_split_a_sentence():
    said, _ = await _feed(["Sono le 17.41 di sera, ok. Vuoi altro?"])
    assert said == ["Sono le 17.41 di sera, ok.", "Vuoi altro?"]
    said, _ = await _feed(["Ok. Ho salvato il fatto che mi hai detto."])
    assert said == ["Ok. Ho salvato il fatto che mi hai detto."]  # "Ok." alone is too short


async def test_markdown_is_removed_from_each_sentence():
    said, _ = await _feed(["**Fatto** il lavoro. Vedi https://x.it per altro."])
    assert said == ["Fatto il lavoro.", "Vedi un link per altro."]


async def test_nothing_is_spoken_once_the_turn_is_interrupted():
    said, s = await _feed(["Una frase lunga abbastanza. ", "Un'altra frase."], stale=lambda: True)
    assert said == [] and not s.spoken


# ---- the handler: one spoken turn through the agent ----


def _handler(make_ctx, llm, notify=None, registry=None, routed=None):
    ctx = make_ctx(llm, registry)

    async def get_ctx():
        return ctx

    return VoiceTurnHandler(get_ctx, routed, notify or AsyncMock()), ctx


def _say_into(said):
    async def say(sentence):
        said.append(sentence)

    return say


async def test_the_answer_is_spoken_plain_and_asked_in_the_spoken_style(tracer, make_ctx):
    llm = FakeLLMClient([LLMResponse(text="Sono le **17:17**. Vedi https://x.it")])
    handler, _ = _handler(make_ctx, llm)
    said = []
    await handler.respond("che ore sono?", TurnClock(), _say_into(said))
    assert said == ["Sono le 17:17.", "Vedi un link"]
    assert VOICE_STYLE in llm.systems[0]


async def test_a_model_that_does_not_stream_is_still_spoken(tracer, make_ctx):
    class Silent(FakeLLMClient):
        async def complete(self, system, messages, tools, on_text=None):
            return await super().complete(system, messages, tools)  # never calls on_text

    handler, _ = _handler(make_ctx, Silent([LLMResponse(text="Risposta intera.")]))
    said = []
    await handler.respond("ciao", TurnClock(), _say_into(said))
    assert said == ["Risposta intera."]


async def test_the_first_sentence_is_spoken_before_the_model_has_finished(tracer, make_ctx):
    release = asyncio.Event()

    class Streaming(FakeLLMClient):
        async def complete(self, system, messages, tools, on_text=None):
            await on_text("Questa è la prima frase. ")
            await release.wait()  # the rest of the reply is still being written
            await on_text("E questa la seconda.")
            return LLMResponse(text="Questa è la prima frase. E questa la seconda.")

    handler, _ = _handler(make_ctx, Streaming([]))
    said, clock = [], TurnClock()
    turn = asyncio.create_task(handler.respond("ciao", clock, _say_into(said)))
    await asyncio.sleep(0.1)
    assert said == ["Questa è la prima frase."] and clock.first_text is not None  # already out
    assert not turn.done()
    release.set()
    await turn
    assert said == ["Questa è la prima frase.", "E questa la seconda."]
    assert clock.answered is not None and clock.answered >= clock.first_text


async def test_a_turn_can_use_a_different_model_without_touching_the_context(tracer, make_ctx):
    main, fast = FakeLLMClient([]), FakeLLMClient([LLMResponse(text="risposta veloce")])
    handler, ctx = _handler(make_ctx, main, routed=fast)
    said = []
    await handler.respond("ciao", TurnClock(), _say_into(said))
    assert said == ["risposta veloce"] and main.calls == []
    assert ctx.llm is main  # the override was for this turn only


async def test_a_confirmation_goes_to_telegram_and_is_only_announced_aloud(tracer, make_ctx):
    executed, notified = [], []

    class Arg(BaseModel):
        text: str = "x"

    async def act(inp: Arg) -> dict:
        executed.append(inp.text)
        return {}

    registry = ToolRegistry()
    registry.register(Tool("act", "an action", Arg, "confirm", act))
    llm = FakeLLMClient([LLMResponse(tool_uses=[ToolUse("1", "act", {"text": "hello"})])])

    async def notify(result: AgentResult) -> None:
        notified.append(result)

    handler, _ = _handler(make_ctx, llm, notify, registry)
    said = []
    await handler.respond("fai l'azione", TurnClock(), _say_into(said))
    assert said == [PENDING_SPEECH]
    assert executed == []  # voice can never settle a confirm-level action
    assert len(notified) == 1 and notified[0].pending is not None


async def test_an_interrupted_turn_lets_the_agent_finish_and_speaks_nothing(tracer, make_ctx):
    gate = asyncio.Event()

    class Slow(FakeLLMClient):
        async def complete(self, system, messages, tools, on_text=None):
            await gate.wait()
            return await super().complete(system, messages, tools, on_text)

    handler, ctx = _handler(make_ctx, Slow([LLMResponse(text="Una risposta tardiva e lunga.")]))
    said = []
    turn = asyncio.create_task(handler.respond("domanda lunga", TurnClock(), _say_into(said)))
    await asyncio.sleep(0.05)
    turn.cancel()  # the user started talking again
    with pytest.raises(asyncio.CancelledError):
        await turn
    gate.set()
    await asyncio.sleep(0.1)
    (run,) = await tracer.list_runs()
    assert run.status == "completed" and run.final_answer == "Una risposta tardiva e lunga."
    assert ctx.history[-1]["role"] == "assistant"  # the history stays consistent
    assert said == []  # and the late reply is never spoken over the user


async def test_latency_is_written_on_the_runs_trace(tracer, make_ctx):
    handler, _ = _handler(make_ctx, FakeLLMClient([LLMResponse(text="ok")]))
    clock = TurnClock(speech_end=1.0, heard=1.3, first_text=1.6, answered=1.9, first_audio=2.0)
    await handler.respond("ciao", clock, _say_into([]))
    await handler.record_latency(clock)
    run = (await tracer.list_runs())[0]
    step = next(s for s in run.steps if s.type == "voice_latency")
    assert step.idx == LATENCY_STEP_IDX and step.output["total_s"] == 1.0
    assert step.duration_ms == 1000


# ---- run_agent: per-run overrides and the per-conversation lock ----


async def test_runs_on_one_conversation_are_serialised(tracer, make_ctx):
    order = []

    class Tracking(FakeLLMClient):
        async def complete(self, system, messages, tools):
            order.append(f"start {messages[-1]['content']}")
            await asyncio.sleep(0.05)
            order.append(f"end {messages[-1]['content']}")
            return await super().complete(system, messages, tools)

    ctx = make_ctx(Tracking([LLMResponse(text="a"), LLMResponse(text="b")]))
    await asyncio.gather(run_agent("uno", ctx), run_agent("due", ctx))
    assert order == ["start uno", "end uno", "start due", "end due"]  # never interleaved


# ---- latency routing ----


async def test_fast_model_answers_simple_requests_alone():
    fast, main = FakeLLMClient([LLMResponse(text="veloce")]), FakeLLMClient([])
    out = await RoutedLLM(fast, main).complete("s", [{"role": "user", "content": "ciao"}], [])
    assert out.text == "veloce" and main.calls == []


async def test_the_main_model_takes_over_when_a_tool_is_needed():
    tool = LLMResponse(tool_uses=[ToolUse("1", "site_status", {})])
    fast, main = FakeLLMClient([tool]), FakeLLMClient([LLMResponse(text="deciso dal principale")])
    out = await RoutedLLM(fast, main).complete("s", [{"role": "user", "content": "il sito?"}], [])
    assert out.text == "deciso dal principale" and len(fast.calls) == 1 and len(main.calls) == 1


async def test_after_tool_results_only_the_main_model_reasons():
    fast, main = FakeLLMClient([]), FakeLLMClient([LLMResponse(text="sintesi")])
    messages = [
        {"role": "user", "content": "il sito?"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "1", "name": "x", "input": {}}],
        },
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]},
    ]
    assert (await RoutedLLM(fast, main).complete("s", messages, [])).text == "sintesi"
    assert fast.calls == []


async def test_a_read_only_tool_call_is_left_to_the_fast_model():
    tool = LLMResponse(tool_uses=[ToolUse("1", "get_current_time", {})])
    fast, main = FakeLLMClient([tool]), FakeLLMClient([])
    router = RoutedLLM(fast, main, frozenset({"get_current_time"}))
    out = await router.complete("s", [{"role": "user", "content": "che ore sono?"}], [])
    assert out.tool_uses[0].name == "get_current_time" and main.calls == []  # no second call


async def test_a_tool_that_can_change_something_is_decided_by_the_main_model():
    tool = LLMResponse(tool_uses=[ToolUse("1", "create_note", {"text": "x"})])
    fast, main = FakeLLMClient([tool]), FakeLLMClient([LLMResponse(text="deciso dal principale")])
    router = RoutedLLM(fast, main, frozenset({"get_current_time"}))  # create_note: not read-only
    out = await router.complete("s", [{"role": "user", "content": "annota x"}], [])
    assert out.text == "deciso dal principale" and len(fast.calls) == 1 and len(main.calls) == 1


def _after_tool(name):
    call = {"type": "tool_use", "id": "1", "name": name, "input": {}}
    result = {"type": "tool_result", "tool_use_id": "1", "content": "ok"}
    return [
        {"role": "user", "content": "domanda"},
        {"role": "assistant", "content": [call]},
        {"role": "user", "content": [result]},
    ]


async def test_the_fast_model_writes_the_answer_after_read_only_results():
    fast, main = FakeLLMClient([LLMResponse(text="sono le otto")]), FakeLLMClient([])
    router = RoutedLLM(fast, main, frozenset({"get_current_time"}))
    out = await router.complete("s", _after_tool("get_current_time"), [])
    assert out.text == "sono le otto" and main.calls == []


async def test_after_a_write_tool_only_the_main_model_reasons():
    fast, main = FakeLLMClient([]), FakeLLMClient([LLMResponse(text="sintesi")])
    router = RoutedLLM(fast, main, frozenset({"get_current_time"}))
    assert (await router.complete("s", _after_tool("create_note"), [])).text == "sintesi"
    assert fast.calls == []


def test_the_registry_lists_its_read_only_tools():
    from app.tools.builtin import default_registry

    reads = default_registry().names_with("read")
    assert "get_current_time" in reads and "recall" in reads
    assert not reads & {"send_email", "create_note", "forget", "merge_pull_request"}


# ---- confirmations shown on Telegram ----


async def test_a_voice_confirmation_is_shown_on_telegram_with_buttons(tracer, make_ctx):
    state = BotState({1}, lambda: make_ctx(FakeLLMClient([])), tracer)
    result = AgentResult(7, "awaiting_confirmation", "Conferma richiesta: fare X")
    result.pending = PendingAction("act", {}, "id", "fare X")
    bot = SimpleNamespace(send_message=AsyncMock())
    await send_confirmation(bot, state, 1, result)
    kwargs = bot.send_message.await_args.kwargs
    assert kwargs["chat_id"] == 1 and kwargs["text"] == "Conferma richiesta: fare X"
    buttons = kwargs["reply_markup"].inline_keyboard[0]
    assert [b.callback_data for b in buttons] == ["ok:7", "no:7"]
    assert state.pending[1] is result and state.last_run[1] == 7  # the click can find it


# ---- web endpoints ----


def _app(sessions=None):
    app = FastAPI()
    app.include_router(build_realtime_router())
    if sessions is not None:
        app.state.realtime = sessions
    return app


async def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_voice_endpoints_are_404_unless_realtime_voice_is_on():
    async with await _client(_app()) as c:
        assert (await c.post("/start", json={})).status_code == 404
        assert (await c.get("/status")).status_code == 404
        assert (await c.post("/api/offer", json={"sdp": "x", "type": "offer"})).status_code == 404


async def test_the_session_scoped_paths_the_web_client_uses_also_work():
    # the prebuilt client posts its offer to /sessions/{sessionId}/api/offer after /start
    sessions = SimpleNamespace(
        offer=AsyncMock(return_value={"sdp": "a", "type": "answer", "pc_id": "p1"}),
        candidate=AsyncMock(return_value={"status": "success"}),
    )
    async with await _client(_app(sessions)) as c:
        r = await c.post("/sessions/abc/api/offer", json={"sdp": "v=0", "type": "offer"})
        assert r.status_code == 200 and r.json()["pc_id"] == "p1"
        patch = {"pc_id": "p1", "candidates": []}
        assert (await c.patch("/sessions/abc/api/offer", json=patch)).status_code == 200
        # camelCase request data from the client is accepted too
        body = {"sdp": "x", "type": "offer", "requestData": {"k": 1}}
        await c.post("/sessions/abc/api/offer", json=body)
        assert sessions.offer.await_args.args[0].request_data == {"k": 1}


async def test_a_malformed_offer_is_a_400_not_a_crash():
    sessions = SimpleNamespace(offer=AsyncMock(), candidate=AsyncMock())
    async with await _client(_app(sessions)) as c:
        assert (await c.post("/api/offer", json={"type": "offer"})).status_code == 400
        assert (await c.post("/api/offer", content=b"not json")).status_code == 400
        assert (await c.patch("/api/offer", json={"candidates": []})).status_code == 400
    sessions.offer.assert_not_called()


async def test_voice_endpoints_delegate_to_the_sessions_when_on():
    sessions = SimpleNamespace(
        start=AsyncMock(return_value={"sessionId": "s1"}),
        offer=AsyncMock(return_value={"sdp": "answer", "type": "answer", "pc_id": "p1"}),
        candidate=AsyncMock(return_value={"status": "success"}),
    )
    async with await _client(_app(sessions)) as c:
        assert (await c.get("/status")).json()["status"] == "ready"
        assert (await c.post("/start", json={"enableDefaultIceServers": True})).json() == {
            "sessionId": "s1"
        }
        r = await c.post("/api/offer", json={"sdp": "v=0", "type": "offer"})
        assert r.json()["pc_id"] == "p1" and sessions.offer.await_args.args[0].sdp == "v=0"
        patch = {
            "pc_id": "p1",
            "candidates": [{"candidate": "c", "sdp_mid": "0", "sdp_mline_index": 0}],
        }
        assert (await c.patch("/api/offer", json=patch)).json() == {"status": "success"}
        assert sessions.candidate.await_args.args[0].candidates[0].sdp_mid == "0"
    sessions.start.assert_awaited_once()


# ---- the real pipeline can be built ----


def test_keyterms_come_from_settings_and_can_be_empty():
    from app.voice.realtime import keyterms

    assert keyterms(SimpleNamespace(stt_keyterms="Jarvis, annulla ,,che ore sono")) == [
        "Jarvis",
        "annulla",
        "che ore sono",
    ]
    assert keyterms(SimpleNamespace(stt_keyterms="")) is None
    assert keyterms(SimpleNamespace()) is None


def test_the_real_pipeline_builds_with_the_expected_stages():
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection

    from app.voice.realtime import build_session

    settings = SimpleNamespace(
        stt_provider="elevenlabs", tts_provider="cartesia", elevenlabs_api_key="k",
        cartesia_api_key="k", tts_model="sonic-3.6", tts_voice="v",
    )  # fmt: skip

    async def noop(*a, **k): ...

    async def build():
        handler = VoiceTurnHandler(noop, None, noop)
        return build_session(SmallWebRTCConnection(ice_servers=None), settings, handler)

    task, runner = asyncio.run(build())
    assert task is not None and runner is not None


def test_realtime_voice_refuses_providers_it_does_not_support():
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection

    from app.voice.realtime import build_session

    settings = SimpleNamespace(stt_provider="cartesia", tts_provider="cartesia")

    async def noop(*a, **k): ...

    async def build():
        return build_session(
            SmallWebRTCConnection(ice_servers=None), settings, VoiceTurnHandler(noop, None, noop)
        )

    with pytest.raises(ValueError, match="STT_PROVIDER=elevenlabs|deepgram"):
        asyncio.run(build())
