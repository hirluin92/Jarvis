import io
import wave

import httpx
import pytest

from app.voice import providers
from app.voice.providers import Audio, ProviderError, audio_seconds, pcm_to_wav
from evals.voice.bench import load_phrases, pct, render_report, slug
from evals.voice.wer import italian_number, normalize, wer


@pytest.mark.parametrize(
    ("n", "word"),
    [
        (0, "zero"), (4, "quattro"), (12, "dodici"), (20, "venti"), (21, "ventuno"),
        (23, "ventitre"), (28, "ventotto"), (80, "ottanta"), (100, "cento"),
        (108, "centotto"), (180, "centottanta"), (234, "duecentotrentaquattro"),
        (1000, "mille"), (2026, "duemilaventisei"), (15000, "quindicimila"),
    ],
)  # fmt: skip
def test_italian_numbers(n, word):
    assert italian_number(n) == word


def test_normalisation_ignores_case_accents_punctuation_and_digits():
    assert normalize("Il ventitré dicembre, alle 20!") == [
        "il",
        "ventitre",
        "dicembre",
        "alle",
        "venti",
    ]
    assert normalize("del 12%") == ["del", "dodici", "per", "cento"]
    assert normalize("l'ultima risposta") == ["l", "ultima", "risposta"]


def test_wer_counts_substitutions_deletions_insertions():
    assert wer("che ore sono", "Che ore sono?") == 0
    assert wer("che ore sono a Tokyo", "che ore sono a Kyoto") == pytest.approx(0.2)
    assert wer("uno due tre quattro", "uno tre quattro") == pytest.approx(0.25)
    assert wer("uno due", "uno due tre") == pytest.approx(0.5)
    assert wer("", "") == 0 and wer("", "qualcosa") == 1


def test_a_digit_transcription_equals_the_spelled_out_reference():
    assert wer("quattro persone alle venti", "4 persone alle 20") == 0


def test_phrases_are_twenty_and_unique():
    phrases = load_phrases()
    assert len(phrases) == 20 and len(set(phrases)) == 20


def test_percentile_and_slug():
    assert pct([1, 2, 3, 4, 100], 0.95) == 100 and pct([], 0.5) == 0.0
    assert slug("cartesia-sonic-3.6 (Elena)") == "cartesia-sonic-3-6-elena"


def test_pcm_is_wrapped_in_a_valid_wav():
    wav = pcm_to_wav(b"\x00\x00" * 16000)  # one second of silence
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 16000)
    assert audio_seconds(Audio(wav, "audio/wav", "wav")) == pytest.approx(1.0)
    assert audio_seconds(Audio(b"x", "audio/mpeg", "mp3")) is None


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_elevenlabs_tts_sends_the_language_only_to_models_that_accept_it():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"mp3-bytes")

    async with _client(handler) as c:
        flash = await providers.tts_elevenlabs(c, "k", "ciao", "voice1", "eleven_flash_v2_5")
        await providers.tts_elevenlabs(c, "k", "ciao", "voice1", "eleven_multilingual_v2")
    assert flash.audio.data == b"mp3-bytes" and flash.first_audio_s >= 0
    assert b'"language_code":"it"' in seen[0].content
    assert b"language_code" not in seen[1].content
    assert seen[0].headers["xi-api-key"] == "k" and "/voice1/stream" in str(seen[0].url)


async def test_cartesia_tts_asks_for_raw_pcm_and_returns_a_wav():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"\x00\x00" * 8000)

    async with _client(handler) as c:
        syn = await providers.tts_cartesia(c, "k", "ciao", "v1", "sonic-3.6")
    body = seen[0].content.decode()
    assert '"container":"raw"' in body and '"language":"it"' in body and '"id":"v1"' in body
    assert seen[0].headers["authorization"] == "Bearer k"
    assert audio_seconds(syn.audio) == pytest.approx(0.5)


async def test_transcription_requests_and_response_parsing():
    audio = Audio(b"abc", "audio/mpeg", "mp3")

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "api.deepgram.com":
            assert request.headers["authorization"] == "Token k"
            assert (
                request.url.params["model"] == "nova-3" and request.url.params["language"] == "it"
            )
            return httpx.Response(
                200, json={"results": {"channels": [{"alternatives": [{"transcript": "uno"}]}]}}
            )
        return httpx.Response(200, json={"text": "due" if "elevenlabs" in host else "tre"})

    async with _client(handler) as c:
        assert (await providers.stt_deepgram(c, "k", audio)).text == "uno"
        assert (await providers.stt_elevenlabs(c, "k", audio)).text == "due"
        assert (await providers.stt_cartesia(c, "k", audio)).text == "tre"


async def test_a_provider_error_never_includes_the_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "bad key"})

    async with _client(handler) as c:
        with pytest.raises(ProviderError) as exc:
            await providers.stt_deepgram(c, "SECRET-KEY-123", Audio(b"x", "audio/wav", "wav"))
        with pytest.raises(ProviderError) as exc2:
            await providers.tts_cartesia(c, "SECRET-KEY-123", "ciao", "v", "m")
    assert "401" in str(exc.value) and "SECRET-KEY-123" not in str(exc.value)
    assert "SECRET-KEY-123" not in str(exc2.value)


def test_report_has_tables_limits_and_the_worst_errors():
    tts = [{"service": "svc", "phrase": 1, "chars": 10, "first_audio_s": 0.2, "total_s": 0.5}]
    stt = [
        {"stt": "a", "source": "sintetico", "phrase": 1, "ref": "uno due", "hyp": "uno",
         "wer": 0.5, "secs": 0.4, "audio_secs": None},
        {"stt": "a", "source": "sintetico", "phrase": 2, "error": "boom"},
    ]  # fmt: skip
    text = render_report(tts, stt, "2026-11-16 10:00")
    assert "| svc | 1 | 200 ms |" in text and "50.0% (n=1)" in text
    assert (
        "Richieste fallite: 1" in text
        and "non in streaming" in text
        and "«uno due» → «uno»" in text
    )
