"""Real-time voice: a browser talks to Jarvis through Pipecat, over WebRTC.

The conversation is the same one as Telegram (same history, memory and action log): Pipecat only
moves audio and decides when a turn starts and ends; the answer always comes from `run_agent`.
Confirmations (`confirm` tools) are never settled by voice: they go to Telegram with buttons.
"""

import asyncio
import logging
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
    answered: float | None = None  # the agent finished
    first_audio: float | None = None  # the first sample of the reply reached the pipeline output

    def as_dict(self) -> dict[str, float | None]:
        def gap(a: float | None, b: float | None) -> float | None:
            return None if a is None or b is None else round(b - a, 3)

        return {
            "transcript_s": gap(self.speech_end, self.heard),
            "agent_s": gap(self.heard, self.answered),
            "voice_s": gap(self.answered, self.first_audio),
            "total_s": gap(self.speech_end, self.first_audio),  # what the user waits
        }


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
    ) -> None:
        self._get_ctx = get_ctx
        self._llm = llm
        self._notify_pending = notify_pending
        self._background: set[asyncio.Task] = set()
        self.last_run_id: int | None = None
        self.last_tracer: Any = None

    async def respond(self, text: str, clock: TurnClock) -> str | None:
        """What to say aloud for this turn, or None. Raises CancelledError if interrupted."""
        ctx = await self._get_ctx()
        self.last_tracer = ctx.tracer
        task = asyncio.create_task(run_agent(text, ctx, style=VOICE_STYLE, llm=self._llm))
        try:
            # shielded: an interruption must not cut the agent mid-run (history, tools, trace);
            # it finishes in the background and its answer is simply not spoken
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            self._background.add(task)
            task.add_done_callback(self._background.discard)
            raise
        clock.answered = time.monotonic()
        self.last_run_id = result.run_id
        if result.pending:
            await self._notify_pending(result)  # buttons on Telegram, never a spoken yes
            return PENDING_SPEECH
        return speakable(result.answer) if result.answer else None

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


def build_session(connection: Any, settings: Any, handler: VoiceTurnHandler) -> tuple[Any, Any]:
    """The Pipecat pipeline of one browser session: audio in -> transcript -> turns -> agent ->
    voice -> audio out. Returns (task, runner)."""
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
    from pipecat.workers.runner import WorkerRunner

    if settings.stt_provider != "elevenlabs" or settings.tts_provider != "cartesia":
        raise ValueError(
            "real-time voice supports STT_PROVIDER=elevenlabs and TTS_PROVIDER=cartesia"
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
                say = await handler.respond(text, clock)
                if say:
                    await self.push_frame(TTSSpeakFrame(say, append_to_context=False))
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
    stt = ElevenLabsRealtimeSTTService(
        api_key=settings.elevenlabs_api_key,
        settings=ElevenLabsRealtimeSTTService.Settings(
            model="scribe_v2_realtime", language=Language.IT
        ),
    )
    tts = CartesiaTTSService(
        api_key=settings.cartesia_api_key,
        settings=CartesiaTTSService.Settings(
            model=settings.tts_model, voice=settings.tts_voice, language="it"
        ),
    )
    aggregators = LLMContextAggregatorPair(
        LLMContext(), user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
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

    async def offer(self, request: Any) -> dict[str, str] | None:
        async def on_connection(connection: Any) -> None:
            task, runner = build_session(connection, self._settings, self._handler)
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
