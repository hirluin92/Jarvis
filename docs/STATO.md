# Stato di avanzamento

## Fase 1 — Fondamenta: COMPLETATA (2026-11-16)

Codice implementato: config, LLM client + fake, registro tool + 3 tool, ciclo agente,
tracing, modelli + migrazione, bot Telegram con conferma, endpoint, eval, README.

### Verifica dal vivo (2026-11-16)

Ambiente: Postgres 16 in Docker, schema alla revisione `0001 (head)`, backend e bot avviati
con `uvicorn`, bot raggiunto da Telegram.

| # | Criterio | Esito |
|---|---|---|
| 1 | docker compose / uv sync / alembic / uvicorn da repo pulito | PASS |
| 2 | "che ore sono a Tokyo?" dal telefono | PASS (run 3: `get_current_time`, `Asia/Tokyo`) |
| 3 | `/last` mostra l'ultimo run | PASS (provato sul run 8; non sul run con `get_current_time`) |
| 4 | `GET /runs/{id}` con step | PASS su Postgres (run 3, 3 step) |
| 5 | Utente non autorizzato ignorato | PASS (test unitario) — non provato con un secondo account |
| 6 | Conferma/Annulla sul tool demo | PASS (Annulla con pulsante: tool non eseguito, run 8; conferma: run 6) |
| 7 | pytest e ruff | PASS (17 test, ruff pulito) |
| 8 | `evals.run` passa 3 scenari | PASS (`site_up`, `smalltalk`, `tokyo_time`) |
| 9 | README | PASS |

Verifiche aggiuntive sul tracing (tabelle `runs` e `steps`):

- Errore di un tool: nel run 7 `http_health_check` fallisce due volte (`UnsupportedProtocol`,
  poi `ConnectError`); l'errore torna al modello e il run si completa.
- Tool `confirm`: si ferma con `awaiting confirmation` (runs 5 e 8) e non parte senza consenso.

### Da proporre a fine fase (non fatto, fuori specifica)

- Il rifiuto di una conferma non lascia traccia: il run resta in `awaiting confirmation`.
  Valutare uno step o uno stato dedicato (regola 4).
- Dopo la conferma il tool viene eseguito in un run separato (run 6), senza richiamare il modello.
- `pyproject.toml`: impostare `asyncio_default_fixture_loop_scope = "function"` per togliere
  il warning di pytest-asyncio.
- Provare il criterio 5 con un secondo account Telegram.

### Ambiente Windows / Docker (storico)

- Docker non si avviava: eseguito `DISM /Online /Cleanup-Image /RestoreHealth`, interrotto per
  errore e poi rilanciato con **successo**.
- `sfc /scannow`: al primo tentativo "impossibile avviare il servizio di ripristino"
  (TrustedInstaller); al secondo **riuscito**: file danneggiati trovati e ripristinati.
- Dopo riavvio, Docker Desktop è partito; `docker compose up -d` e
  `uv run alembic upgrade head` completati.
- Crediti Anthropic e `TELEGRAM_ALLOWED_USER_IDS` risolti.

## Fase 2 — Report mattutino: IN CORSO (2026-11-16)

Scelte: come prodotto di prova c'è solo **Jarvis** (nessun prodotto reale, nessun Stripe).
`db_metric` e `stripe_summary` NON implementati: non c'è un database di prodotto né un
account Stripe da leggere. Si aggiungono quando servono.

Implementato: catalogo `config/products.yaml`, tool `site_status` e `github_activity` (solo
`GITHUB_READ_TOKEN`), prompt del report, pianificatore giornaliero (07:30 Europe/Rome,
configurabile), comando `/report`, 8 scenari di eval (`report_*`).

| # | Criterio | Esito |
|---|---|---|
| 1 | Report all'orario per 3 giorni di fila | DA VERIFICARE (serve tempo reale; logica testata) |
| 2 | URL spento segnalato in cima | PARZIALE: tool segnala l'URL giù (test + eval `report_site_down`); da provare sul report vero |
| 3 | Ogni numero corrisponde a un output di tool nel tracing | PASS (run 10: 200/78 ms, 0 commit, 0 PR, 0 workflow, tutti negli step) |
| 4 | Secondo prodotto solo da `products.yaml` | PASS (test `test_catalog`, eval con catalogo di prova) |
| 5 | Nessuna credenziale con permessi di scrittura | PASS (usato solo `GITHUB_READ_TOKEN`; `GITHUB_WRITE_TOKEN` non è letto dal codice) |
| 6 | Almeno 8 nuovi scenari, con dati mancanti e sorgente non raggiungibile | PASS (8 scenari, 11 totali, tutti PASS) |

Note: il repo `hirluin92/Jarvis` su GitHub è vuoto (nessun push): il report vede 0 commit.
GitHub risponde 409 sui commit di un repo vuoto: ora gestito come 0 commit, non come errore.

Le fasi 3-7 NON sono iniziate.
