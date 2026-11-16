from datetime import time
from functools import lru_cache
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "gemini"  # gemini (free tier) | anthropic
    anthropic_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    gemini_fast_model: str = "gemini-3.5-flash-lite"
    anthropic_model: str = "claude-sonnet-5-5"
    database_url: str
    telegram_bot_token: str
    telegram_allowed_user_ids: Annotated[list[int], NoDecode] = []
    agent_max_iterations: int = 8
    tool_timeout_seconds: float = 30
    github_read_token: str = Field("", validation_alias="GH_READ_TOKEN")
    github_write_token: str = Field("", validation_alias="GH_WRITE_TOKEN")
    # real-time voice (web page + Pipecat). Off by default: it opens a microphone endpoint.
    realtime_voice: bool = False
    fast_model: str = "claude-haiku-5-5"  # answers simple spoken requests first
    # words the real-time transcription should favour (comma separated)
    stt_keyterms: str = "Jarvis,annulla,conferma,Telegram,GitHub,Gmail,promemoria,che ore sono"
    # voice: providers are chosen here, not in code (see docs/voce-benchmark.md)
    stt_provider: str = "deepgram"  # elevenlabs | deepgram | cartesia
    tts_provider: str = "cartesia"  # cartesia | elevenlabs
    tts_voice: str = "ee16f140-f6dc-490e-a1ed-c1d537ea0086"  # Cartesia "Lorenzo", Italian
    tts_model: str = "sonic-3.6"
    deepgram_api_key: str = ""
    elevenlabs_api_key: str = ""
    cartesia_api_key: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""
    products_file: str = "config/products.yaml"
    report_time: time = time(7, 30)
    report_timezone: str = "Europe/Rome"

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def _split_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return [int(x) for x in v.split(",") if x.strip()]
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
