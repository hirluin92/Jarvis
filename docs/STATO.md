# Stato di avanzamento

## Fase 1 — Fondamenta: IN CORSO (bloccata)

Aggiornamento: token Telegram valido (bot @hirluin_jarvis_bot). Chiave Anthropic valida ma
l'API risponde "credit balance is too low": servono crediti. Mancano ancora
TELEGRAM_ALLOWED_USER_IDS e Docker.

Codice implementato: config, LLM client + fake, registro tool + 3 tool, ciclo agente,
tracing, modelli + migrazione, bot Telegram con conferma, endpoint, eval, README.

| # | Criterio | Esito |
|---|---|---|
| 1 | docker compose / uv sync / alembic / uvicorn da repo pulito | NON VERIFICATO — Docker non installato su questa macchina (`uv sync` ok) |
| 2 | "che ore sono a Tokyo?" dal telefono | NON VERIFICATO — mancano ANTHROPIC_API_KEY e TELEGRAM_BOT_TOKEN |
| 3 | `/last` mostra get_current_time | NON VERIFICATO — idem (logica coperta da codice, non da test e2e) |
| 4 | `GET /runs/{id}` con step | NON VERIFICATO su Postgres; tracing testato su SQLite |
| 5 | Utente non autorizzato ignorato | PASS (test unitario) — da riprovare con un secondo account |
| 6 | Conferma/Annulla sul tool demo | PARZIALE — loop testato; flusso a pulsanti non provato su Telegram |
| 7 | pytest e ruff | PASS (17 test, ruff pulito) |
| 8 | `evals.run` passa 3 scenari | NON VERIFICATO — serve ANTHROPIC_API_KEY |
| 9 | README | PASS |

Le fasi 2-7 NON sono iniziate: il cancello della Fase 1 non è chiuso.
