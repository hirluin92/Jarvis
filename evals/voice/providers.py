"""Speech services under test. Plain async functions, one per service, injectable HTTP client."""

import io
import time
import wave
from dataclasses import dataclass
from typing import Any

import httpx

CARTESIA_VERSION = "2026-08-14"
PCM_RATE = 16000


class ProviderError(RuntimeError):
    pass


@dataclass
class Audio:
    data: bytes
    mime: str
    ext: str


@dataclass
class Synthesis:
    audio: Audio
    first_audio_s: float  # time until the first audio bytes arrived (what a listener waits for)
    total_s: float


@dataclass
class Transcription:
    text: str
    secs: float


def pcm_to_wav(pcm: bytes, rate: int = PCM_RATE) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


def audio_seconds(audio: Audio) -> float | None:
    if audio.ext != "wav":
        return None
    with wave.open(io.BytesIO(audio.data)) as w:
        return w.getnframes() / w.getframerate()


async def _post_stream(
    client: httpx.AsyncClient, url: str, headers: dict[str, str], body: dict[str, Any],
    params: dict[str, str] | None = None,
) -> tuple[bytes, float, float]:  # fmt: skip
    t0 = time.monotonic()
    first: float | None = None
    chunks: list[bytes] = []
    async with client.stream("POST", url, headers=headers, json=body, params=params) as r:
        if r.status_code >= 400:
            raise ProviderError(f"{url.split('//')[1].split('/')[0]} HTTP {r.status_code}: "
                                f"{(await r.aread())[:160]!r}")  # fmt: skip
        async for chunk in r.aiter_bytes():
            if chunk and first is None:
                first = time.monotonic() - t0
            chunks.append(chunk)
    return b"".join(chunks), first or 0.0, time.monotonic() - t0


async def tts_elevenlabs(
    client: httpx.AsyncClient, key: str, text: str, voice: str, model: str
) -> Synthesis:
    body: dict[str, Any] = {"text": text, "model_id": model}
    if "v2_5" in model or "turbo" in model:
        body["language_code"] = "it"  # only these models take an explicit language
    data, first, total = await _post_stream(
        client,
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream",
        {"xi-api-key": key},
        body,
        {"output_format": "mp3_44100_128"},
    )
    return Synthesis(Audio(data, "audio/mpeg", "mp3"), first, total)


async def tts_cartesia(
    client: httpx.AsyncClient, key: str, text: str, voice: str, model: str
) -> Synthesis:
    data, first, total = await _post_stream(
        client,
        "https://api.cartesia.ai/tts/bytes",
        {"Authorization": f"Bearer {key}", "Cartesia-Version": CARTESIA_VERSION},
        {
            "model_id": model,
            "transcript": text,
            "voice": {"id": voice},
            "language": "it",
            "output_format": {"container": "raw", "encoding": "pcm_s16le", "sample_rate": PCM_RATE},
        },
    )
    return Synthesis(Audio(pcm_to_wav(data), "audio/wav", "wav"), first, total)


def _check(r: httpx.Response, service: str) -> None:
    if r.status_code >= 400:
        raise ProviderError(f"{service} HTTP {r.status_code}: {r.text[:160]!r}")


async def stt_deepgram(client: httpx.AsyncClient, key: str, audio: Audio) -> Transcription:
    t0 = time.monotonic()
    r = await client.post(
        "https://api.deepgram.com/v1/listen",
        params={"model": "nova-3", "language": "it", "smart_format": "true"},
        headers={"Authorization": f"Token {key}", "Content-Type": audio.mime},
        content=audio.data,
    )
    _check(r, "deepgram")
    text = r.json()["results"]["channels"][0]["alternatives"][0]["transcript"]
    return Transcription(text, time.monotonic() - t0)


async def stt_elevenlabs(
    client: httpx.AsyncClient, key: str, audio: Audio, model: str = "scribe_v1"
) -> Transcription:
    t0 = time.monotonic()
    r = await client.post(
        "https://api.elevenlabs.io/v1/speech-to-text",
        headers={"xi-api-key": key},
        data={"model_id": model, "language_code": "it"},
        files={"file": (f"audio.{audio.ext}", audio.data, audio.mime)},
    )
    _check(r, "elevenlabs-stt")
    return Transcription(r.json()["text"], time.monotonic() - t0)


async def stt_cartesia(client: httpx.AsyncClient, key: str, audio: Audio) -> Transcription:
    t0 = time.monotonic()
    r = await client.post(
        "https://api.cartesia.ai/stt",
        headers={"Authorization": f"Bearer {key}", "Cartesia-Version": CARTESIA_VERSION},
        data={"model": "ink-whisper", "language": "it"},
        files={"file": (f"audio.{audio.ext}", audio.data, audio.mime)},
    )
    _check(r, "cartesia-stt")
    return Transcription(r.json()["text"], time.monotonic() - t0)


# name -> (function, key setting, extra arguments)
TTS_SERVICES: dict[str, tuple[Any, str, dict[str, str]]] = {
    "elevenlabs-multilingual-v2 (Alice)": (
        tts_elevenlabs, "elevenlabs_api_key",
        {"voice": "Xb7hH8MSUJpSbSDYk0k2", "model": "eleven_multilingual_v2"},
    ),
    "elevenlabs-flash-v2.5 (Alice)": (
        tts_elevenlabs, "elevenlabs_api_key",
        {"voice": "Xb7hH8MSUJpSbSDYk0k2", "model": "eleven_flash_v2_5"},
    ),
    "cartesia-sonic-3.6 (Elena)": (
        tts_cartesia, "cartesia_api_key",
        {"voice": "00e9ec78-2002-41dd-8d19-6b1d3b17a461", "model": "sonic-3.6"},
    ),
    "cartesia-sonic-3.6 (Lorenzo)": (
        tts_cartesia, "cartesia_api_key",
        {"voice": "ee16f140-f6dc-490e-a1ed-c1d537ea0086", "model": "sonic-3.6"},
    ),
}  # fmt: skip
STT_SERVICES: dict[str, tuple[Any, str]] = {
    "deepgram-nova-3": (stt_deepgram, "deepgram_api_key"),
    "elevenlabs-scribe": (stt_elevenlabs, "elevenlabs_api_key"),
    "cartesia-ink-whisper": (stt_cartesia, "cartesia_api_key"),
}
