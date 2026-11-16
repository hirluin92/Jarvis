import re
from typing import Any

import httpx

from app.voice.providers import (
    Audio,
    Synthesis,
    Transcription,
    stt_cartesia,
    stt_deepgram,
    stt_elevenlabs,
    tts_cartesia,
    tts_elevenlabs,
)

STT_PROVIDERS: dict[str, tuple[Any, str]] = {
    "elevenlabs": (stt_elevenlabs, "elevenlabs_api_key"),
    "deepgram": (stt_deepgram, "deepgram_api_key"),
    "cartesia": (stt_cartesia, "cartesia_api_key"),
}
TTS_PROVIDERS: dict[str, tuple[Any, str]] = {
    "cartesia": (tts_cartesia, "cartesia_api_key"),
    "elevenlabs": (tts_elevenlabs, "elevenlabs_api_key"),
}
SPOKEN_MAX_CHARS = 500  # the full answer is always sent as text; speech is billed per character


class VoiceService:
    """Speech in and out, with the providers chosen by settings. Raises ValueError if unusable."""

    def __init__(self, settings: Any, client: httpx.AsyncClient) -> None:
        self._client = client
        self._settings = settings
        self._stt, stt_key = self._pick(STT_PROVIDERS, settings.stt_provider, "STT_PROVIDER")
        self._tts, tts_key = self._pick(TTS_PROVIDERS, settings.tts_provider, "TTS_PROVIDER")
        self._stt_key, self._tts_key = getattr(settings, stt_key), getattr(settings, tts_key)
        for attr, value in ((stt_key, self._stt_key), (tts_key, self._tts_key)):
            if not value:
                raise ValueError(f"{attr.upper()} is not configured")

    @staticmethod
    def _pick(table: dict[str, tuple[Any, str]], name: str, setting: str) -> tuple[Any, str]:
        if name not in table:
            raise ValueError(f"{setting}={name!r} is not one of: {', '.join(table)}")
        return table[name]

    async def transcribe(self, audio: Audio) -> Transcription:
        return await self._stt(self._client, self._stt_key, audio)

    async def speak(self, text: str) -> Synthesis:
        s = self._settings
        extra = {"audio_format": "mp3"} if s.tts_provider == "cartesia" else {}
        return await self._tts(
            self._client, self._tts_key, text, voice=s.tts_voice, model=s.tts_model, **extra
        )


def speakable(text: str, max_chars: int = SPOKEN_MAX_CHARS) -> str:
    """The text of an answer made fit for a voice: no markdown, no raw links, bounded length."""
    text = re.sub(r"https?://\S+", "un link", text)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"^[\s>*\-•#]+", "", text, flags=re.M)  # bullets, quotes, headings
    text = re.sub(r"[*_`#]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind("."), cut.rfind("!"), cut.rfind("?"))
    return cut[: end + 1] if end > max_chars // 3 else cut.rsplit(" ", 1)[0]
