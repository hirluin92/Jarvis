# Jarvis

Assistente personale ad agenti (Telegram + FastAPI + Postgres). Vedi `CLAUDE.md` e `docs/`.

## Installazione

```
cp .env.example .env        # compila ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS
docker compose up -d        # Postgres 16
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload   # ascolta solo su 127.0.0.1
```

## Variabili d'ambiente

| Variabile | Descrizione |
|---|---|
| `ANTHROPIC_API_KEY` | chiave API (obbligatoria) |
| `ANTHROPIC_MODEL` | default `claude-sonnet-5-5` |
| `DATABASE_URL` | Postgres, driver async (obbligatoria) |
| `TELEGRAM_BOT_TOKEN` | token del bot (obbligatoria) |
| `TELEGRAM_ALLOWED_USER_IDS` | ID separati da virgola |
| `AGENT_MAX_ITERATIONS` | default 8 |
| `TOOL_TIMEOUT_SECONDS` | default 30 |

## Comandi

- `uv run pytest` — test (nessuna chiamata API reale)
- `uv run ruff check . && uv run ruff format .` — lint e formato
- `uv run python -m evals.run` — eval contro il modello reale (usa un DB SQLite temporaneo)
- Bot: `/start`, `/reset`, `/last`. API: `GET /health`, `/runs?limit=20`, `/runs/{id}`.
