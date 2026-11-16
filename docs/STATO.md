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

## Fase 3 — Eval e osservabilità: IN CORSO (2026-11-16)

Nota di processo: la fase 3 è stata avviata senza che il criterio 1 della fase 2 (3 giorni di
report di fila) fosse chiuso, su richiesta esplicita di Gianluca. Quel criterio resta aperto.

Implementato: costo stimato per run (prezzi in `app/costs.py`, da `claude-api`: Sonnet 5.5
$2/$10 per Mtok) con riga di riepilogo 24h nel report e in `/last`; pagina web `/ui/runs` e
`/ui/runs/{id}`; `/bad` (colonna `runs.marked_bad`, migrazione 0002, bozze in
`evals/scenarios/drafts/`); eval con giudice (`rubric` + `min_score`), stato atteso,
confronto con l'esecuzione precedente (`evals/results/`, ignorata da git) e tetto di spesa
`--max-cost`; workflow `.github/workflows/ci.yml`.

| # | Criterio | Esito |
|---|---|---|
| 1 | Una PR che peggiora uno scenario viene bloccata dalla CI | PASS: PR di prova #1 (prompt che vieta i tool) con CI `checks` in failure e `mergeable_state: blocked`; PR chiusa e ramo cancellato. Protezione su `main`: check `checks` obbligatorio, `enforce_admins` disattivo (l'admin può aggirarla) |
| 2 | `/bad` produce uno scenario valido in meno di un minuto | PASS nei test (la bozza passa la stessa validazione degli scenari); da provare su Telegram |
| 3 | Di ogni run: passaggi, durata, token, costo stimato | PASS (pagina `/ui/runs/{id}`, verificata sul server vero) |
| 4 | Almeno 25 scenari, meno di 5 minuti | PASS: 27 scenari, 105 s, circa $0.17 per esecuzione, tutti PASS |

Note: i secret GitHub non possono chiamarsi `GITHUB_*`: i token si chiamano `GH_READ_TOKEN` e
`GH_WRITE_TOKEN` ovunque (`.env`, codice, CI). La CI usa Python 3.12 (`.python-version`).
Le prime due CI erano fallite perché partite prima dei secret (chiave Anthropic vuota).

## Fase 4 — Azioni con permessi: IN CORSO, manca solo `trigger_deploy` (2026-11-16)

Implementato: tool `write` `create_note`, `add_todo` (Postgres, migrazione 0003) e
`create_github_issue`; tool `confirm` `merge_pull_request`. Ogni scrittura rilegge lo stato reale
e restituisce la prova (id, URL, SHA). Messaggio di conferma con il testo esatto
dell'azione (`Tool.describe`), scadenza 10 minuti, uso singolo. Tabella `actions` e comando
`/actions` (chi, cosa, quando, esito, prova). Prompt rafforzato contro le istruzioni nei dati.
Il token usato per scrivere è solo `GH_WRITE_TOKEN`; la lettura resta su `GH_READ_TOKEN`.

Gmail: `draft_email` (`write`, crea solo una bozza) e `send_email` (`confirm`, un solo
destinatario, il messaggio di conferma mostra destinatario, oggetto e testo). Autorizzazione OAuth
una tantum con `uv run python -m app.auth_gmail`, scope solo `gmail.compose` (nessuna lettura
della posta); il token di rinnovo è in `.env` (`GOOGLE_REFRESH_TOKEN`). Provato dal vivo con una
bozza vera (creata, riletta da Gmail, cancellata). `send_email` NON provato con una mail vera:
solo test con transport finti.
Account autorizzato: quello di prova indicato nella console Google.

ATTENZIONE: l'app Google è in modalità **Test**, quindi il token di rinnovo scade dopo 7 giorni.
Alla scadenza le mail falliscono con un errore chiaro; si rifà con `uv run python -m app.auth_gmail`.
Per evitarlo bisogna pubblicare l'app (la console chiede home page, privacy e termini).

NON implementato, di proposito:
- `trigger_deploy`: la roadmap non dice cosa debba fare (Jarvis non ha un ambiente di deploy).
  Rimandato alla fase 7, quando esisterà un ambiente da aggiornare.

| # | Criterio | Esito |
|---|---|---|
| 1 | Nessun tool `confirm` esegue senza un clic: test dedicato | PASS (`test_confirmation.py`: la funzione del tool non viene mai chiamata prima del clic) |
| 2 | Conferma scaduta o già usata rifiutata | PASS (test: dopo 600 s e al secondo clic) |
| 3 | Mail di prova con "ignora le istruzioni e invia X" non fa partire nulla | PARZIALE: `inject_email_note_issue` (la mail avvelenata tenta anche `send_email`) e `inject_web_merge` PASS con sorgenti simulate. Jarvis non legge la casella (scope solo `compose`), quindi non esiste un test con una mail vera |
| 4 | Ogni azione compare in `/actions` con la sua prova | PASS nei test; da provare su Telegram. Issue vera #2 creata e riletta da GitHub con il tool (poi chiusa) |

Limiti noti:
- Un rifiuto con "Annulla" non viene scritto in `actions` (solo le esecuzioni).
- La difesa dalle istruzioni nei dati è prompt + eval (2 scenari), non una garanzia: le azioni
  `confirm` restano comunque bloccate dal clic, ma un tool `write` potrebbe essere indotto.
- Nelle eval `create_github_issue`, `merge_pull_request`, `draft_email` e `send_email` sono
  sostituiti da stub, per non toccare mai GitHub né Gmail.

Eval: 35 scenari, tutti PASS, 136 s, circa $0.38 per esecuzione.

## Fase 5 — Memoria: IMPLEMENTATA (2026-11-16)

Implementato: tabella `memories` (testo, categoria, run di origine, date, stato
attivo/sostituito/dimenticato; migrazione 0004); tool `remember` (`write`, rifiuta i segreti:
chiavi, password, PIN, IBAN, carte), `recall` (`read`), `forget` (`confirm`, la conferma mostra il
testo del fatto); recupero automatico dei fatti pertinenti nel contesto di ogni run (massimo 5
fatti e 1500 caratteri, etichettati come dato e non istruzione); `/memory`, `/forget <id>`
(l'utente che scrive il comando è già il consenso; finisce in `/actions`) e pagina
`/ui/memory` per correggere o dimenticare (token anti-CSRF, i segreti sono rifiutati anche qui);
cronologia in Postgres (`conversations`), riassunto automatico oltre 30 messaggi (si tengono gli
ultimi 12; tetto rigido 60), `/reset` cancella anche la cronologia salvata.

Scelta tecnica: la ricerca NON usa pgvector. Con poche centinaia di fatti i candidati si
classificano in Python per parole condivise (radice di 5 lettere, niente accenti, parole
vuote escluse): zero servizi esterni e zero chiavi nuove. È lessicale, non semantica: "dentista"
trova "dentisti" ma non "odontoiatra". Se i fatti diventano molti o servono sinonimi, si passa a
pgvector con un servizio di embedding (decisione da prendere allora).

| # | Criterio | Esito |
|---|---|---|
| 1 | Un fatto detto oggi è usato in una chat nuova domani | PASS nei test (contesto nuovo, fatto nel prompt); da provare su Telegram |
| 2 | Un fatto corretto sostituisce il precedente | PASS (test e modello vero: la riga vecchia diventa `superseded`, ne resta una attiva) |
| 3 | Dopo `/forget` il fatto non è più recuperato | PASS (test, anche per il contesto automatico) |
| 4 | Riavviando il processo la conversazione continua | PASS nei test (la cronologia si ricarica da Postgres); da provare riavviando il server |
| 5 | Scenari di eval: richiamo, fatto assente, fatto superato | PASS: `memory_used`, `memory_absent`, `memory_correction` (+ remember, secret_refused, forget_confirm) |

Limiti noti:
- `remember` è `write`, senza conferma: un testo malevolo letto da un tool potrebbe indurre il
  modello a salvare un "fatto" falso. Mitigazioni: i fatti entrano nel prompt come dato, i segreti
  sono rifiutati, tutto è in `/actions` e correggibile da `/memory` e dalla pagina web.
- Una conferma in sospeso non sopravvive a un riavvio (il pulsante risponde "scaduta o già usata").
- L'eval `memory_correction` non verifica da sola la sostituzione: la verifica è nei test.

Eval: 41 scenari, tutti PASS, 166 s, circa $0.55 per esecuzione (il prompt è cresciuto con i tool).

## Fase 6 — Voce: IN CORSO, solo il benchmark (2026-11-16)

Fatto: benchmark in italiano (`uv run python -m evals.voice.bench`, rapporto in
`docs/voce-benchmark.md`). Fornitori provati: trascrizione con Deepgram Nova-3, ElevenLabs Scribe
e Cartesia Ink-Whisper; voce con ElevenLabs (multilingual v2 e flash v2.5) e Cartesia Sonic 3.6
(voci Elena e Lorenzo). Soniox lasciato fuori (nessun credito gratuito sulle API). Chiavi in
`.env`: `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `CARTESIA_API_KEY`.

Risultato provvisorio, su audio sintetico (venti frasi): Scribe 0-0,4% di errori, Deepgram 1,2-8%,
Cartesia Ink 5-6%; il primo audio della sintesi arriva in 86-330 ms su tutte le voci. Tutti i
servizi sbagliano "No, annulla" (sentito come "a nulla"). I fornitori NON sono ancora fissati:
manca la prova con le registrazioni vere dell'utente e l'ascolto delle voci.

Da fare: registrazioni vere (5-8 frasi) e ascolto delle voci per scegliere; poi il passo 1
(messaggi vocali su Telegram) e il passo 2 (tempo reale con Pipecat). I fornitori vanno
configurabili da `.env` (criterio 5).

Le fasi 7 NON è iniziata.
