# Benchmark vocale in italiano

Generato da `evals/voice/bench.py` il 2026-11-16 11:38. Venti frasi in italiano (`evals/voice/phrases.yaml`). Errore di parola (WER) calcolato dopo normalizzazione: maiuscole, accenti, punteggiatura e cifre scritte in lettere non contano come errori.

## Sintesi vocale (testo → voce)

| Servizio | Frasi | Primo audio medio | Primo audio p95 | Tempo totale medio |
|---|---|---|---|---|
| elevenlabs-multilingual-v2 (Alice) | 20 | 328 ms | 422 ms | 370 ms |
| elevenlabs-flash-v2.5 (Alice) | 20 | 161 ms | 172 ms | 234 ms |
| cartesia-sonic-3.6 (Elena) | 20 | 156 ms | 125 ms | 616 ms |
| cartesia-sonic-3.6 (Lorenzo) | 20 | 86 ms | 109 ms | 535 ms |

Il primo audio è quello che conta in una conversazione: è l'attesa prima che la voce inizi. L'obiettivo della roadmap è una risposta che inizia entro circa 1,5 secondi (tra trascrizione, modello e voce, quindi il budget della sola voce è molto più piccolo).

## Trascrizione (voce → testo)

WER medio (più basso è meglio), per servizio e per origine dell'audio:

| Servizio | elevenlabs-multilingual-v2 (Alice) | elevenlabs-flash-v2.5 (Alice) | cartesia-sonic-3.6 (Elena) | cartesia-sonic-3.6 (Lorenzo) | registrazioni reali | Latenza media | Latenza p95 |
|---|---|---|---|---|---|---|---|
| deepgram-nova-3 | 8.0% (n=20) | 8.0% (n=20) | 1.2% (n=20) | 1.2% (n=20) | 17.6% (n=8) | 0.19 s | 0.25 s |
| elevenlabs-scribe | 0.0% (n=20) | 1.0% (n=20) | 0.0% (n=20) | 0.0% (n=20) | 3.4% (n=8) | 0.60 s | 0.81 s |
| cartesia-ink-whisper | 6.4% (n=20) | 6.4% (n=20) | 5.4% (n=20) | 6.2% (n=20) | 16.0% (n=8) | 0.22 s | 0.31 s |

Gli errori più gravi:

- **deepgram-nova-3** (elevenlabs-multilingual-v2 (Alice)), frase 12, WER 100%: «No, annulla.» → «A nulla.»
- **deepgram-nova-3** (elevenlabs-flash-v2.5 (Alice)), frase 12, WER 100%: «No, annulla.» → «No, a nulla.»
- **deepgram-nova-3** (registrazioni reali), frase 12, WER 100%: «No, annulla.» → «No, a nulla.»
- **cartesia-ink-whisper** (elevenlabs-multilingual-v2 (Alice)), frase 12, WER 100%: «No, annulla.» → «No a nulla.»
- **cartesia-ink-whisper** (elevenlabs-flash-v2.5 (Alice)), frase 12, WER 100%: «No, annulla.» → «No a nulla.»
- **cartesia-ink-whisper** (cartesia-sonic-3.6 (Elena)), frase 12, WER 100%: «No, annulla.» → «No, a nulla.»

## Limiti di questo confronto

- Le latenze di trascrizione sono di richieste complete su un file, **non in streaming**: indicano la velocità relativa, non il ritardo reale in una conversazione. Il ritardo in streaming si misura nel passo 2 (tempo reale).
- L'audio sintetico è pulito e regolare: separa i servizi scarsi da quelli buoni, ma non sostituisce le registrazioni vere. Le colonne 'registrazioni reali' vengono dalla voce dell'utente.
- Le voci ElevenLabs disponibili col piano usato sono tutte americane o britanniche, non italiane native: l'accento della sintesi ne risente.
- Con venti frasi la misura ha un margine ampio: differenze di pochi punti percentuali non sono significative.
