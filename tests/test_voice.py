from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import BaseModel
from telegram.ext import MessageHandler

from app.agent.llm import FakeLLMClient, LLMResponse, ToolUse
from app.agent.prompts import VOICE_STYLE
from app.interfaces.telegram import BotState, build_handlers
from app.tools.registry import Tool, ToolRegistry
from app.voice.providers import Audio, ProviderError, Synthesis, Transcription, tts_cartesia
from app.voice.service import SPOKEN_MAX_CHARS, VoiceService, speakable


def _settings(**kw):
    base = {
        "stt_provider": "elevenlabs",
        "tts_provider": "cartesia",
        "tts_voice": "v1",
        "tts_model": "sonic-3.6",
        "elevenlabs_api_key": "el",
        "deepgram_api_key": "dg",
        "cartesia_api_key": "ca",
    }
    return SimpleNamespace(**{**base, **kw})


# ---- speakable ----


def test_speakable_removes_markdown_and_links():
    text = "**Fatto.**\n- punto uno\n- punto due\nVedi https://example.com/x e `codice`."
    assert speakable(text) == "Fatto. punto uno punto due Vedi un link e codice."


def test_speakable_cuts_a_long_answer_at_a_sentence_end():
    long = "Prima frase completa. " * 40
    out = speakable(long)
    assert len(out) <= SPOKEN_MAX_CHARS and out.endswith(".")


def test_speakable_cuts_at_a_word_when_there_is_no_sentence_end():
    out = speakable("parola " * 200, max_chars=50)
    assert len(out) <= 50 and not out.endswith(" ")


# ---- provider choice from settings ----


def test_providers_come_from_settings_and_missing_keys_are_reported():
    async def handler(request):
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert VoiceService(_settings(stt_provider="deepgram"), client)
    with pytest.raises(ValueError, match="STT_PROVIDER='nope'"):
        VoiceService(_settings(stt_provider="nope"), client)
    with pytest.raises(ValueError, match="TTS_PROVIDER='nope'"):
        VoiceService(_settings(tts_provider="nope"), client)
    with pytest.raises(ValueError, match="CARTESIA_API_KEY"):
        VoiceService(_settings(cartesia_api_key=""), client)
    with pytest.raises(ValueError, match="DEEPGRAM_API_KEY"):
        VoiceService(_settings(stt_provider="deepgram", deepgram_api_key=""), client)


async def test_stt_provider_setting_routes_the_request_to_that_service():
    hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        if request.url.host == "api.deepgram.com":
            return httpx.Response(
                200, json={"results": {"channels": [{"alternatives": [{"transcript": "ok"}]}]}}
            )
        return httpx.Response(200, json={"text": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        cases = {
            "elevenlabs": "api.elevenlabs.io",
            "deepgram": "api.deepgram.com",
            "cartesia": "api.cartesia.ai",
        }
        for provider, host in cases.items():
            service = VoiceService(_settings(stt_provider=provider), c)
            assert (await service.transcribe(Audio(b"x", "audio/ogg", "ogg"))).text == "ok"
            assert hosts[-1] == host


async def test_cartesia_speech_is_requested_as_mp3_for_telegram():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"mp3")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        said = await VoiceService(_settings(), c).speak("ciao")
        assert said.audio.ext == "mp3" and said.audio.data == b"mp3"
        assert b'"container":"mp3"' in seen[0].content and b'"id":"v1"' in seen[0].content
        wav = await tts_cartesia(c, "k", "ciao", "v", "m")  # the benchmark default stays WAV
        assert wav.audio.ext == "wav"


# ---- the Telegram voice path ----


class FakeVoice:
    def __init__(self, text="che ore sono a Tokyo", fail=False):
        self.text, self.fail = text, fail
        self.heard = []
        self.spoken = []

    async def transcribe(self, audio):
        if self.fail:
            raise ProviderError("boom")
        self.heard.append(audio)
        return Transcription(self.text, 0.1)

    async def speak(self, text):
        self.spoken.append(text)
        return Synthesis(Audio(b"mp3-bytes", "audio/mpeg", "mp3"), 0.1, 0.2)


def _voice_update(user_id: int):
    downloaded = AsyncMock(return_value=bytearray(b"ogg-bytes"))
    tg_file = SimpleNamespace(download_as_bytearray=downloaded)
    msg = SimpleNamespace(
        text=None,
        voice=SimpleNamespace(get_file=AsyncMock(return_value=tg_file)),
        reply_text=AsyncMock(),
        reply_voice=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_message=msg,
        effective_chat=SimpleNamespace(id=user_id, send_action=AsyncMock()),
    )
    return update, msg


def _on_voice(state):
    handlers = [h for h in build_handlers(state) if isinstance(h, MessageHandler)]
    return handlers[0].callback  # the VOICE handler is registered before the text one


def _state(tracer, make_ctx, llm, voice, registry=None):
    return BotState({1}, lambda: make_ctx(llm, registry), tracer, voice=voice)


async def test_voice_message_is_transcribed_answered_in_text_and_spoken(tracer, make_ctx):
    voice = FakeVoice()
    llm = FakeLLMClient([LLMResponse(text="A Tokyo sono le **17:17**.")])
    state = _state(tracer, make_ctx, llm, voice)
    update, msg = _voice_update(1)
    await _on_voice(state)(update, None)
    texts = [c.args[0] for c in msg.reply_text.await_args_list]
    assert texts == [
        "🎤 «che ore sono a Tokyo»",
        "A Tokyo sono le **17:17**.",
    ]  # heard, then answer
    assert voice.heard[0].data == b"ogg-bytes" and voice.heard[0].ext == "ogg"
    assert voice.spoken == ["A Tokyo sono le 17:17."]  # markdown removed for the voice
    msg.reply_voice.assert_awaited_once_with(voice=b"mp3-bytes")
    assert VOICE_STYLE in llm.systems[0]  # spoken requests get the short-answer style


async def test_the_spoken_style_is_not_applied_to_typed_messages(tracer, make_ctx):
    llm = FakeLLMClient([LLMResponse(text="ok"), LLMResponse(text="ok")])
    state = _state(tracer, make_ctx, llm, FakeVoice())
    update, msg = _voice_update(1)
    await _on_voice(state)(update, None)
    text_handler = build_handlers(state)[-1].callback
    msg2 = SimpleNamespace(text="ciao", reply_text=AsyncMock())
    update2 = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_message=msg2,
        effective_chat=SimpleNamespace(id=1, send_action=AsyncMock()),
    )
    await text_handler(update2, None)
    assert VOICE_STYLE in llm.systems[0] and VOICE_STYLE not in llm.systems[1]


async def test_unauthorized_voice_message_is_ignored_before_any_download(tracer, make_ctx):
    voice = FakeVoice()
    state = _state(tracer, make_ctx, FakeLLMClient([]), voice)
    update, msg = _voice_update(999)
    await _on_voice(state)(update, None)
    msg.voice.get_file.assert_not_called()
    msg.reply_text.assert_not_called()
    assert voice.heard == []


async def test_without_a_voice_service_voice_messages_are_ignored(tracer, make_ctx):
    state = _state(tracer, make_ctx, FakeLLMClient([]), None)
    update, msg = _voice_update(1)
    await _on_voice(state)(update, None)
    msg.voice.get_file.assert_not_called()


async def test_empty_transcription_asks_to_repeat(tracer, make_ctx):
    state = _state(tracer, make_ctx, FakeLLMClient([]), FakeVoice(text="  "))
    update, msg = _voice_update(1)
    await _on_voice(state)(update, None)
    msg.reply_text.assert_awaited_once_with("Non ho capito: puoi ripetere?")
    msg.reply_voice.assert_not_called()


async def test_a_transcription_failure_is_reported_not_raised(tracer, make_ctx):
    state = _state(tracer, make_ctx, FakeLLMClient([]), FakeVoice(fail=True))
    update, msg = _voice_update(1)
    await _on_voice(state)(update, None)
    assert "Non sono riuscito a trascrivere" in msg.reply_text.await_args.args[0]


async def test_a_failing_voice_reply_leaves_the_text_answer_in_place(tracer, make_ctx):
    class NoVoice(FakeVoice):
        async def speak(self, text):
            raise ProviderError("tts down")

    state = _state(tracer, make_ctx, FakeLLMClient([LLMResponse(text="risposta")]), NoVoice())
    update, msg = _voice_update(1)
    await _on_voice(state)(update, None)
    assert msg.reply_text.await_args_list[-1].args[0] == "risposta"
    msg.reply_voice.assert_not_called()


async def test_a_confirmation_asked_by_voice_is_shown_on_screen_and_never_spoken(tracer, make_ctx):
    executed = []

    class Arg(BaseModel):
        text: str = "x"

    async def act(inp: Arg) -> dict:
        executed.append(inp.text)
        return {}

    registry = ToolRegistry()
    registry.register(Tool("act", "an action", Arg, "confirm", act))
    llm = FakeLLMClient([LLMResponse(tool_uses=[ToolUse("1", "act", {"text": "hello"})])])
    voice = FakeVoice(text="fai l'azione")
    state = _state(tracer, make_ctx, llm, voice, registry)
    update, msg = _voice_update(1)
    await _on_voice(state)(update, None)
    assert executed == []  # a spoken request cannot run a confirm-level tool
    last = msg.reply_text.await_args_list[-1]
    assert "Conferma richiesta" in last.args[0] and last.kwargs.get("reply_markup") is not None
    assert voice.spoken == [] and 1 in state.pending  # waiting for the button, not for voice
    msg.reply_voice.assert_not_called()
