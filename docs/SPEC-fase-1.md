# Fase 1 — Fondamenta

## Obiettivo

Scrivo un messaggio al bot Telegram dal telefono, l'agente ragiona, usa uno o più tool
e mi risponde. Ogni passaggio è registrato su Postgres e consultabile.

Nessuna funzione "utile" in questa fase: serve lo scheletro su cui si appoggia tutto il resto.

## Fuori dallo scopo

Voce, memoria a lungo termine, dashboard web, tool che scrivono su sistemi esterni,
integrazioni con i prodotti, deploy su server. Arrivano nelle fasi successive.

## Componenti

### 1. Configurazione (`app/config.py`)

Impostazioni lette da variabili d'ambiente con pydantic-settings:

| Variabile | Descrizione |
|---|---|
| `ANTHROPIC_API_KEY` | chiave API |
| `ANTHROPIC_MODEL` | modello da usare, default `claude-sonnet-5-5` |
| `DATABASE_URL` | connessione Postgres (driver async) |
| `TELEGRAM_BOT_TOKEN` | token del bot |
| `TELEGRAM_ALLOWED_USER_IDS` | elenco di ID separati da virgola |
| `AGENT_MAX_ITERATIONS` | default 8 |
| `TOOL_TIMEOUT_SECONDS` | default 30 |

L'app non parte se manca una variabile obbligatoria, con un errore chiaro.

### 2. Client del modello (`app/agent/llm.py`)

- Protocollo `LLMClient` con un metodo async che riceve system prompt, messaggi e
  definizioni dei tool, e restituisce blocchi di testo, richieste di tool e uso dei token.
- `AnthropicClient`: implementazione con l'SDK ufficiale (Messages API, tool use).
- `FakeLLMClient` per i test: restituisce risposte predefinite in sequenza.

### 3. Registro dei tool (`app/tools/registry.py`)

- Un tool si registra con: `name`, `description`, modello Pydantic di input,
  `permission` (`read` | `write` | `confirm`), funzione async.
- Il registro genera le definizioni dei tool nel formato richiesto dall'API a partire
  dagli schemi Pydantic.
- Prima dell'esecuzione l'input viene validato; un input non valido torna al modello
  come errore leggibile.
- Tool iniziali, tutti `read`:
  - `get_current_time` — data e ora in un fuso orario (default Europe/Rome)
  - `http_health_check` — fa una GET a un URL e restituisce stato e tempo di risposta
  - `echo_confirm_demo` — tool finto con permesso `confirm`, serve solo a provare il
    flusso di conferma

### 4. Ciclo agente (`app/agent/loop.py`)

Funzione `run_agent(user_message, context) -> AgentResult`:

1. Crea un `Run` nel tracing.
2. Chiama il modello con system prompt, cronologia e tool.
3. Se il modello chiede dei tool: li esegue (con timeout), aggiunge i risultati ai
   messaggi e torna al punto 2.
4. Se un tool ha permesso `confirm`: **non lo esegue**, chiude il run in stato
   `awaiting_confirmation` e restituisce la descrizione dell'azione da confermare.
5. Si ferma quando il modello risponde senza chiedere tool, o al raggiungimento di
   `AGENT_MAX_ITERATIONS` (in quel caso risponde spiegando che si è fermato).
6. Un'eccezione in un tool diventa un risultato di errore per il modello, non un crash.

La cronologia della conversazione in questa fase resta in memoria per chat, con un
limite agli ultimi 20 messaggi.

### 5. Tracing (`app/tracing.py`, `app/db/models.py`)

Tabelle:

- `runs`: id, inizio, fine, stato (`running` | `completed` | `failed` |
  `awaiting_confirmation` | `max_iterations`), messaggio utente, risposta finale,
  token in ingresso e in uscita totali, modello.
- `steps`: id, run_id, indice, tipo (`llm_call` | `tool_call`), nome del tool,
  input (JSON), output (JSON), errore, durata in ms, token.

Migrazione Alembic iniziale inclusa.

### 6. Bot Telegram (`app/interfaces/telegram.py`)

- Long polling, avviato e fermato con il ciclo di vita di FastAPI.
- Ignora e registra i messaggi da utenti non in `TELEGRAM_ALLOWED_USER_IDS`.
- Ogni messaggio di testo chiama `run_agent` e invia la risposta.
- Mostra "sta scrivendo" durante l'elaborazione.
- Se il run è `awaiting_confirmation`, invia la descrizione dell'azione con due
  pulsanti, **Conferma** e **Annulla**; alla conferma esegue il tool e completa il run.
- Comandi: `/start` (saluto), `/reset` (svuota la cronologia), `/last` (riepilogo
  dell'ultimo run: tool usati, durata, token).

### 7. Backend (`app/main.py`)

- `GET /health` — stato dell'app e della connessione al database.
- `GET /runs?limit=20` — ultimi run.
- `GET /runs/{id}` — un run con tutti i suoi step.

Gli endpoint ascoltano solo su localhost in questa fase.

### 8. Eval (`evals/`)

- Formato di uno scenario YAML: `name`, `input`, `expected_tools` (tool che devono
  essere chiamati), `forbidden_tools`, `answer_must_contain` (opzionale).
- `evals/run.py` esegue tutti gli scenari contro il modello reale e stampa una tabella
  con esito, tool chiamati, durata e token.
- Tre scenari iniziali:
  1. "Che ore sono a Tokyo?" → deve chiamare `get_current_time`
  2. "Il sito https://example.com è su?" → deve chiamare `http_health_check`
  3. "Ciao, come va?" → non deve chiamare nessun tool

## Test richiesti (pytest, senza chiamate API reali)

- Registro: registrazione, generazione dello schema, rifiuto di input non valido.
- Ciclo agente con `FakeLLMClient`: risposta diretta; una chiamata a un tool; più
  chiamate in sequenza; errore di un tool gestito; limite di iterazioni; tool
  `confirm` non eseguito senza conferma.
- Tracing: un run con due step viene salvato e riletto correttamente.
- Telegram: un utente non autorizzato viene ignorato.

## Criteri di accettazione

1. `docker compose up -d`, `uv sync`, `uv run alembic upgrade head` e l'avvio di
   uvicorn funzionano da repo pulito seguendo solo il README.
2. Dal telefono, "che ore sono a Tokyo?" riceve la risposta corretta entro pochi secondi.
3. `/last` mostra che è stato usato `get_current_time`.
4. `GET /runs/{id}` restituisce il run con gli step di modello e tool.
5. Un messaggio da un altro account Telegram non riceve risposta.
6. Chiedendo di usare il tool demo compare la richiesta di conferma, e con **Annulla**
   il tool non viene eseguito.
7. `uv run pytest` e `uv run ruff check .` passano.
8. `uv run python -m evals.run` passa i tre scenari.
9. Il README spiega installazione, variabili d'ambiente e comandi.

## Ordine di implementazione consigliato

1. Scheletro del repo, `pyproject.toml`, Docker Compose, configurazione, `/health`
2. Modelli del database, migrazione, tracing
3. Registro dei tool e i tre tool iniziali, con test
4. `LLMClient`, client finto, ciclo agente, con test
5. Bot Telegram e flusso di conferma
6. Endpoint `/runs`, eval, README

Un commit per punto. Fermati dopo ogni punto se qualcosa nella specifica non torna.
