"""Real-time voice: a browser talks to Jarvis through Pipecat, over WebRTC.

The conversation is the same one as Telegram (same history, memory and action log): Pipecat only
moves audio and decides when a turn starts and ends; the answer always comes from `run_agent`.
Confirmations (`confirm` tools) are never settled by voice: they go to Telegram with buttons.
"""

import asyncio
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.agent.llm import LLMClient
from app.agent.loop import AgentContext, AgentResult, run_agent
from app.agent.prompts import VOICE_STYLE
from app.voice.service import speakable

log = logging.getLogger("jarvis.realtime")

PENDING_SPEECH = (
    "Ti ho mandato la richiesta di conferma su Telegram: premi il pulsante per procedere."
)
LATENCY_STEP_IDX = 1000  # a trace step after the run's own steps


@dataclass
class TurnClock:
    """When each stage of one spoken turn happened (monotonic seconds)."""

    speech_end: float | None = None  # the user stopped talking (voice activity detector)
    heard: float | None = None  # the turn text was ready (final transcript + turn rules)
    first_text: float | None = None  # the first sentence of the reply was ready to be spoken
    answered: float | None = None  # the agent finished the whole reply
    first_audio: float | None = None  # the first sample of the reply reached the pipeline output

    def as_dict(self) -> dict[str, float | None]:
        def gap(a: float | None, b: float | None) -> float | None:
            return None if a is None or b is None else round(b - a, 3)

        return {
            "transcript_s": gap(self.speech_end, self.heard),
            "agent_s": gap(self.heard, self.first_text),  # until the first words exist
            "voice_s": gap(self.first_text, self.first_audio),
            "total_s": gap(self.speech_end, self.first_audio),  # what the user waits
            "reply_done_s": gap(self.heard, self.answered),  # the whole reply, for reference
        }


_SENTENCE_END = re.compile(r"[.!?…]+[\"»”)]*\s+")
MIN_SENTENCE_CHARS = 12  # do not cut "Ok." or "dott." into a sentence of its own


class Sentencer:
    """Cuts the streamed reply into sentences and hands each one over as soon as it is complete,
    so the voice starts on the first sentence while the rest is still being written."""

    def __init__(self, say: Callable[[str], Awaitable[None]], is_stale: Callable[[], bool]) -> None:
        self._say, self._is_stale = say, is_stale
        self._buf = ""
        self.spoken = False  # whether anything was handed to the voice

    async def feed(self, piece: str) -> None:
        self._buf += piece
        while (m := _SENTENCE_END.search(self._buf, MIN_SENTENCE_CHARS)) is not None:
            sentence, self._buf = self._buf[: m.end()], self._buf[m.end() :]
            await self._speak(sentence)

    async def flush(self) -> None:
        rest, self._buf = self._buf, ""
        await self._speak(rest)

    async def _speak(self, text: str) -> None:
        if self._is_stale():  # the user interrupted: a late sentence must not be spoken
            return
        said = speakable(text)
        if said:
            self.spoken = True
            await self._say(said)


def keyterms(settings: Any) -> list[str] | None:
    """Words and phrases the transcription should favour (STT_KEYTERMS, comma separated)."""
    terms = [t.strip() for t in str(getattr(settings, "stt_keyterms", "")).split(",") if t.strip()]
    return terms or None


def turn_text(frame: Any) -> str | None:
    """The user's text carried by a context frame, or None when it must not be answered.

    Speculative frames are provisional turns that may be withdrawn: answering them would speak
    over the user and run tools for a request that was never confirmed.
    """
    if getattr(frame, "speculation", False):
        return None
    for message in reversed(frame.context.messages):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, list):
                content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
            return content.strip() or None if isinstance(content, str) else None
    return None


class VoiceTurnHandler:
    """Answers one spoken turn through the agent. No audio and no Pipecat in here."""

    def __init__(
        self,
        get_ctx: Callable[[], Awaitable[AgentContext]],
        llm: LLMClient | None,
        notify_pending: Callable[[AgentResult], Awaitable[None]],
        events: Any = None,
    ) -> None:
        self._get_ctx = get_ctx
        self._llm = llm
        self._notify_pending = notify_pending
        self._events = events  # EventBus of the live screen, optional
        self._background: set[asyncio.Task] = set()
        self.last_run_id: int | None = None
        self.last_tracer: Any = None

    async def respond(
        self, text: str, clock: TurnClock, say: Callable[[str], Awaitable[None]]
    ) -> None:
        """Answer one spoken turn, handing sentences to `say` as they are written.
        Raises CancelledError if the user interrupted (nothing more is spoken then)."""
        ctx = await self._get_ctx()
        self.last_tracer = ctx.tracer
        interrupted = False
        self._emit("heard", text=text)
        self._emit("thinking")

        async def speak(sentence: str) -> None:
            if clock.first_text is None:
                clock.first_text = time.monotonic()
            self._emit("say", text=sentence)
            await say(sentence)

        sentencer = Sentencer(speak, lambda: interrupted)
        task = asyncio.create_task(
            run_agent(text, ctx, style=VOICE_STYLE, llm=self._llm, on_text=sentencer.feed)
        )
        try:
            # shielded: an interruption must not cut the agent mid-run (history, tools, trace);
            # it finishes in the background and nothing it writes is spoken any more
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
            self._emit("interrupted")
            self._background.add(task)
            task.add_done_callback(self._background.discard)
            raise
        clock.answered = time.monotonic()
        self.last_run_id = result.run_id
        await self._emit_tools(ctx, result.run_id)
        if result.pending:
            self._emit("pending", text=result.pending.label())
            await self._notify_pending(result)  # buttons on Telegram, never a spoken yes
            await speak(PENDING_SPEECH)
            self._emit("done")
            return
        await sentencer.flush()
        if not sentencer.spoken and result.answer:  # a model that did not stream
            await speak(speakable(result.answer))
        self._emit("done")

    def _emit(self, kind: str, **data: Any) -> None:
        if self._events is not None:
            self._events.publish(kind, **data)

    async def _emit_tools(self, ctx: AgentContext, run_id: int) -> None:
        if self._events is None:
            return
        run = await ctx.tracer.get_run(run_id)
        names = [s.tool_name for s in (run.steps if run else []) if s.type == "tool_call"]
        if names:
            self._emit("tools", names=names)

    async def record_latency(self, clock: TurnClock) -> None:
        """Write the latency breakdown of the turn on the run's trace."""
        if self.last_run_id is None or self.last_tracer is None:
            return
        parts = clock.as_dict()
        total = parts["total_s"]
        await self.last_tracer.add_step(
            self.last_run_id,
            LATENCY_STEP_IDX,
            "voice_latency",
            output=parts,
            duration_ms=int((total or 0) * 1000),
        )
        log.info("voice turn latency: %s", parts)


def build_session(
    connection: Any, settings: Any, handler: VoiceTurnHandler, mute_while_speaking: bool = False
) -> tuple[Any, Any]:
    """The Pipecat pipeline of one browser session: audio in -> transcript -> turns -> agent ->
    voice -> audio out. Returns (task, runner).

    `mute_while_speaking`: ignore the microphone while Jarvis talks (no interruption by voice).
    Used for phones, where the speaker feeds back into the microphone and Jarvis answers himself."""
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.frames.frames import (
        LLMContextFrame,
        TTSAudioRawFrame,
        TTSSpeakFrame,
        VADUserStoppedSpeakingFrame,
    )
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
    )
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.services.cartesia.tts import CartesiaTTSService
    from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
    from pipecat.transcriptions.language import Language
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
    from pipecat.turns.user_mute.always_user_mute_strategy import AlwaysUserMuteStrategy
    from pipecat.turns.user_stop import TurnAnalyzerUserTurnStopStrategy
    from pipecat.turns.user_turn_strategies import UserTurnStrategies
    from pipecat.workers.runner import WorkerRunner

    if (
        settings.stt_provider not in ("elevenlabs", "deepgram")
        or settings.tts_provider != "cartesia"
    ):
        raise ValueError(
            "real-time voice supports STT_PROVIDER=elevenlabs|deepgram and TTS_PROVIDER=cartesia"
        )

    state = {"clock": None}  # the turn in progress

    class Brain(FrameProcessor):
        """Replaces the LLM slot: the user's turn goes to Jarvis' agent, the answer to the voice."""

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, VADUserStoppedSpeakingFrame):
                state["clock"] = TurnClock(speech_end=time.monotonic())
                await self.push_frame(frame, direction)
            elif isinstance(frame, LLMContextFrame):
                text = turn_text(frame)
                if text is None:
                    return  # provisional or empty turn: say nothing
                clock = state["clock"] or TurnClock(speech_end=time.monotonic())
                clock.heard = time.monotonic()
                state["clock"] = clock

                async def say(sentence: str) -> None:
                    await self.push_frame(TTSSpeakFrame(sentence, append_to_context=False))

                await handler.respond(text, clock, say)
            else:
                await self.push_frame(frame, direction)

    class Probe(FrameProcessor):
        """Notes when the first reply audio appears, then reports where the time went."""

        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            clock = state["clock"]
            if isinstance(frame, TTSAudioRawFrame) and clock and clock.first_audio is None:
                clock.first_audio = time.monotonic()
                asyncio.create_task(handler.record_latency(clock))
            await self.push_frame(frame, direction)

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(audio_in_enabled=True, audio_out_enabled=True),
    )
    if settings.stt_provider == "deepgram":
        from pipecat.services.deepgram.stt import DeepgramSTTService

        stt = DeepgramSTTService(
            api_key=settings.deepgram_api_key,
            settings=DeepgramSTTService.Settings(
                model="nova-3", language=Language.IT, keyterm=keyterms(settings), smart_format=True
            ),
        )
    else:
        stt = ElevenLabsRealtimeSTTService(
            api_key=settings.elevenlabs_api_key,
            settings=ElevenLabsRealtimeSTTService.Settings(
                model="scribe_v2_realtime", language=Language.IT, keyterms=keyterms(settings)
            ),
        )
    tts = CartesiaTTSService(
        api_key=settings.cartesia_api_key,
        settings=CartesiaTTSService.Settings(
            model=settings.tts_model, voice=settings.tts_voice, language="it"
        ),
    )
    # end of turn: the voice detector says when sound stops, a small model (bundled with Pipecat)
    # says whether the sentence is really over, so a pause mid-sentence does not split the request
    turn_stop = TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())
    aggregators = LLMContextAggregatorPair(
        LLMContext(),
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=UserTurnStrategies(stop=[turn_stop]),
            user_mute_strategies=[AlwaysUserMuteStrategy()] if mute_while_speaking else [],
        ),
    )
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            Brain(),
            tts,
            Probe(),
            transport.output(),
            aggregators.assistant(),
        ]
    )
    task = PipelineWorker(pipeline)

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_transport, _client):
        await task.cancel()

    return task, WorkerRunner(handle_sigint=False)


class RealtimeSessions:
    """FastAPI side: the endpoints the browser client talks to, one live session at a time."""

    def __init__(self, settings: Any, handler: VoiceTurnHandler) -> None:
        from pipecat.transports.smallwebrtc.request_handler import (
            ConnectionMode,
            SmallWebRTCRequestHandler,
        )

        self._settings = settings
        self._handler = handler
        self._web = SmallWebRTCRequestHandler(connection_mode=ConnectionMode.SINGLE)
        self._tasks: set[asyncio.Task] = set()

    async def start(self, body: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {"sessionId": str(uuid.uuid4())}
        if body.get("enableDefaultIceServers"):
            result["iceConfig"] = {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
        return result

    async def offer(self, request: Any, remote: bool = False) -> dict[str, str] | None:
        # a remote client (the phone, through Tailscale) hears itself: no voice interruption there
        mute = remote and not getattr(self._settings, "remote_barge_in", False)
        if not getattr(request, "pc_id", None) and getattr(self._web, "_pcs_map", None):
            # a brand new call while an old one is still being torn down (a dropped phone that
            # reconnects): the new one wins, instead of being refused with a 400
            await self._web.close()

        async def on_connection(connection: Any) -> None:
            task, runner = build_session(connection, self._settings, self._handler, mute)
            run = asyncio.create_task(runner.run(task))
            self._tasks.add(run)
            run.add_done_callback(self._tasks.discard)

        return await self._web.handle_web_request(request, on_connection)

    async def candidate(self, request: Any) -> dict[str, str]:
        await self._web.handle_patch_request(request)
        return {"status": "success"}

    async def close(self) -> None:
        await self._web.close()
        for t in list(self._tasks):
            t.cancel()
