"""Italian voice benchmark: speech-to-text error rate and latency, text-to-speech latency.

  uv run python -m evals.voice.bench                      # synthetic audio from every TTS voice
  uv run python -m evals.voice.bench --recordings DIR     # real recordings named 01.ogg .. 20.ogg

Synthetic audio is clean, so it only separates bad services from good ones; the final choice
rests on real recordings of the user's own voice (see docs/voce-benchmark.md).
"""

import argparse
import asyncio
import json
import math
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from app.config import get_settings
from app.voice.providers import (
    Audio,
    ProviderError,
    audio_seconds,
    stt_cartesia,
    stt_deepgram,
    stt_elevenlabs,
    tts_cartesia,
    tts_elevenlabs,
)
from evals.voice.wer import wer

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

HERE = Path(__file__).parent
AUDIO_DIR = HERE / "audio"  # cached synthesis, git-ignored
RESULTS_DIR = HERE / "results"  # raw results, git-ignored
REPORT = Path("docs/voce-benchmark.md")
MIMES = {
    "wav": "audio/wav", "mp3": "audio/mpeg", "m4a": "audio/mp4", "ogg": "audio/ogg",
    "oga": "audio/ogg", "opus": "audio/ogg", "webm": "audio/webm", "flac": "audio/flac",
}  # fmt: skip


def load_phrases() -> list[str]:
    return yaml.safe_load((HERE / "phrases.yaml").read_text(encoding="utf-8"))["phrases"]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else 0.0


async def synthesize_all(
    client: httpx.AsyncClient, settings: Any, phrases: list[str]
) -> tuple[dict[str, list[tuple[int, Audio]]], list[dict[str, Any]]]:
    sources: dict[str, list[tuple[int, Audio]]] = {}
    rows: list[dict[str, Any]] = []
    for name, (func, key_attr, extra) in TTS_SERVICES.items():
        key = getattr(settings, key_attr)
        if not key:
            print(f"[tts] {name}: salto, manca {key_attr.upper()}")
            continue
        folder = AUDIO_DIR / slug(name)
        folder.mkdir(parents=True, exist_ok=True)
        sources[name] = []
        for i, text in enumerate(phrases, 1):
            meta = folder / f"{i:02d}.json"
            existing = next(iter(folder.glob(f"{i:02d}.[!j]*")), None)
            try:
                if existing and meta.exists():  # cached: no new credits spent, same timings
                    audio = Audio(
                        existing.read_bytes(), MIMES[existing.suffix[1:]], existing.suffix[1:]
                    )
                    timing = json.loads(meta.read_text())
                else:
                    syn = await func(client, key, text, **extra)
                    audio = syn.audio
                    timing = {"first_audio_s": syn.first_audio_s, "total_s": syn.total_s}
                    (folder / f"{i:02d}.{audio.ext}").write_bytes(audio.data)
                    meta.write_text(json.dumps(timing))
            except ProviderError as e:
                print(f"[tts] {name} frase {i}: {e}")
                rows.append({"service": name, "phrase": i, "error": str(e)})
                continue
            sources[name].append((i, audio))
            rows.append({"service": name, "phrase": i, "chars": len(text), **timing})
        print(f"[tts] {name}: {len(sources[name])}/{len(phrases)} frasi")
    return sources, rows


def load_recordings(folder: Path) -> list[tuple[int, Audio]]:
    found = []
    for p in sorted(folder.iterdir()):
        m = re.fullmatch(r"(\d{1,2})", p.stem)
        if m and p.suffix[1:].lower() in MIMES:
            found.append(
                (
                    int(m.group(1)),
                    Audio(p.read_bytes(), MIMES[p.suffix[1:].lower()], p.suffix[1:].lower()),
                )
            )
    return found


async def transcribe_all(
    client: httpx.AsyncClient, settings: Any, phrases: list[str],
    sources: dict[str, list[tuple[int, Audio]]],
) -> list[dict[str, Any]]:  # fmt: skip
    rows: list[dict[str, Any]] = []
    for stt_name, (func, key_attr) in STT_SERVICES.items():
        key = getattr(settings, key_attr)
        if not key:
            print(f"[stt] {stt_name}: salto, manca {key_attr.upper()}")
            continue
        for source, items in sources.items():
            done = 0
            for i, audio in items:
                ref = phrases[i - 1]
                try:
                    tr = await func(client, key, audio)
                except ProviderError as e:
                    rows.append({"stt": stt_name, "source": source, "phrase": i, "error": str(e)})
                    print(f"[stt] {stt_name} / {source} frase {i}: {e}")
                    continue
                done += 1
                rows.append({
                    "stt": stt_name, "source": source, "phrase": i, "ref": ref, "hyp": tr.text,
                    "wer": wer(ref, tr.text), "secs": tr.secs, "audio_secs": audio_seconds(audio),
                })  # fmt: skip
            print(f"[stt] {stt_name} / {source}: {done}/{len(items)} frasi")
    return rows


def render_report(
    tts_rows: list[dict[str, Any]], stt_rows: list[dict[str, Any]], stamp: str
) -> str:
    out = [
        "# Benchmark vocale in italiano",
        "",
        f"Generato da `evals/voice/bench.py` il {stamp}. Venti frasi in italiano "
        "(`evals/voice/phrases.yaml`). Errore di parola (WER) calcolato dopo normalizzazione: "
        "maiuscole, accenti, punteggiatura e cifre scritte in lettere non contano come errori.",
        "",
        "## Sintesi vocale (testo → voce)",
        "",
        "| Servizio | Frasi | Primo audio medio | Primo audio p95 | Tempo totale medio |",
        "|---|---|---|---|---|",
    ]
    for name in dict.fromkeys(r["service"] for r in tts_rows):
        ok = [r for r in tts_rows if r["service"] == name and "error" not in r]
        bad = sum(1 for r in tts_rows if r["service"] == name and "error" in r)
        if not ok:
            out.append(f"| {name} | 0 (errori: {bad}) | - | - | - |")
            continue
        first = [r["first_audio_s"] * 1000 for r in ok]
        total = [r["total_s"] * 1000 for r in ok]
        count = f"{len(ok)} (errori: {bad})" if bad else str(len(ok))
        out.append(
            f"| {name} | {count} | {statistics.mean(first):.0f} ms | "
            f"{pct(first, 0.95):.0f} ms | {statistics.mean(total):.0f} ms |"
        )
    out += [
        "",
        "Il primo audio è quello che conta in una conversazione: è l'attesa prima che la voce "
        "inizi. L'obiettivo della roadmap è una risposta che inizia entro circa 1,5 secondi "
        "(tra trascrizione, modello e voce, quindi il budget della sola voce è molto più piccolo).",
        "",
        "## Trascrizione (voce → testo)",
        "",
    ]
    good = [r for r in stt_rows if "error" not in r]
    sources = list(dict.fromkeys(r["source"] for r in good))
    services = list(dict.fromkeys(r["stt"] for r in stt_rows))
    out += ["WER medio (più basso è meglio), per servizio e per origine dell'audio:", ""]
    out += ["| Servizio | " + " | ".join(sources) + " | Latenza media | Latenza p95 |"]
    out += ["|---|" + "---|" * (len(sources) + 2)]
    for svc in services:
        cells = []
        for src in sources:
            w = [r["wer"] for r in good if r["stt"] == svc and r["source"] == src]
            cells.append(f"{statistics.mean(w) * 100:.1f}% (n={len(w)})" if w else "-")
        secs = [r["secs"] for r in good if r["stt"] == svc]
        lat = f"{statistics.mean(secs):.2f} s | {pct(secs, 0.95):.2f} s" if secs else "- | -"
        out.append(f"| {svc} | " + " | ".join(cells) + f" | {lat} |")
    errors = [r for r in stt_rows if "error" in r]
    if errors:
        out += ["", f"Richieste fallite: {len(errors)} (vedi i risultati grezzi)."]
    worst = sorted(good, key=lambda r: r["wer"], reverse=True)[:6]
    if worst and worst[0]["wer"] > 0:
        out += ["", "Gli errori più gravi:", ""]
        for r in worst:
            out.append(f"- **{r['stt']}** ({r['source']}), frase {r['phrase']}, WER "
                       f"{r['wer'] * 100:.0f}%: «{r['ref']}» → «{r['hyp']}»")  # fmt: skip
    out += [
        "",
        "## Limiti di questo confronto",
        "",
        "- Le latenze di trascrizione sono di richieste complete su un file, **non in streaming**: "
        "indicano la velocità relativa, non il ritardo reale in una conversazione. Il ritardo in "
        "streaming si misura nel passo 2 (tempo reale).",
        "- L'audio sintetico è pulito e regolare: separa i servizi scarsi da quelli buoni, ma "
        "non sostituisce le registrazioni vere. Le colonne 'registrazioni reali' vengono "
        "dalla voce dell'utente.",
        "- Le voci ElevenLabs disponibili col piano usato sono tutte americane o britanniche, "
        "non italiane native: l'accento della sintesi ne risente.",
        "- Con venti frasi la misura ha un margine ampio: differenze di pochi punti percentuali "
        "non sono significative.",
        "",
    ]
    return "\n".join(out)


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    phrases = load_phrases()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    tts_rows: list[dict[str, Any]] = []
    sources: dict[str, list[tuple[int, Audio]]] = {}
    async with httpx.AsyncClient(timeout=90) as client:
        if args.synthetic or not args.recordings:
            sources, tts_rows = await synthesize_all(client, settings, phrases)
        if args.recordings:
            real = load_recordings(Path(args.recordings))
            print(f"[reg] {len(real)} registrazioni trovate in {args.recordings}")
            sources["registrazioni reali"] = real
        stt_rows = await transcribe_all(client, settings, phrases, sources)
    RESULTS_DIR.mkdir(exist_ok=True)
    raw = RESULTS_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.json"
    raw.write_text(
        json.dumps({"tts": tts_rows, "stt": stt_rows}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    REPORT.write_text(render_report(tts_rows, stt_rows, stamp), encoding="utf-8")
    print(f"Rapporto scritto in {REPORT}; risultati grezzi in {raw}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--recordings", help="folder with real recordings named 01.ogg .. 20.ogg")
    ap.add_argument("--synthetic", action="store_true", help="also run synthetic audio")
    sys.exit(asyncio.run(main(ap.parse_args())))
