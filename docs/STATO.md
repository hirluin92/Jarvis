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

## Fase 6 — Voce: IN CORSO, passo 1 fatto (2026-11-16)

### Decisione sui fornitori (dopo la prova con la voce dell'utente, 8 registrazioni)

- **Trascrizione: ElevenLabs Scribe** (`STT_PROVIDER=elevenlabs`). Errore medio 3,4% sulle
  registrazioni vere, contro 17,6% di Deepgram Nova-3 e 16,0% di Cartesia Ink-Whisper. Decisivo
  "No, annulla.": Deepgram e Cartesia sentono "No, a nulla" (100% di errore), Scribe lo trascrive
  giusto. Scribe è però il più lento in batch (0,60 s contro 0,19-0,22 s): il ritardo vero in
  streaming si misura nel passo 2.
- **Voce: Cartesia Sonic 3.6, voce Lorenzo** (scelta dell'utente all'ascolto; è nativa italiana
  e ha il primo audio più rapido, 86 ms). Il piano gratuito vale circa 27 minuti di voce al mese:
  per un uso quotidiano serve il Pro (5 dollari al mese).
- Tutto è configurabile da `.env` (`STT_PROVIDER`, `TTS_PROVIDER`, `TTS_VOICE`, `TTS_MODEL`).

Il campione è piccolo (8 frasi): la scelta si regge sul caso di "annulla" e sul divario ampio, non
sulle differenze di pochi punti. Alcune "differenze" sono di formato, non di ascolto: Scribe scrive
"9:00" dove si è detto "nove". Un errore comune a Deepgram e Scribe ("trasferita/sono trasferito")
può essere la voce registrata e non il servizio.

### Passo 1: messaggi vocali su Telegram (fatto)

Il bot accetta i vocali: li trascrive e mostra cosa ha capito (🎤 «…»), risponde in testo e poi in
voce (breve, senza markdown né link; massimo 500 caratteri parlati). Un vocale non conferma mai
un'azione `confirm`: la richiesta compare sullo schermo coi pulsanti e non viene detta a voce. Un
utente non autorizzato non fa scaricare nulla. Se la trascrizione è vuota chiede di ripetere. Se
manca una chiave vocale, il testo funziona e i vocali sono ignorati (avviso nel log).

Criteri della fase 6: 1 (un vocale riceve risposta corretta in testo e audio) da provare su
Telegram; 2, 3 (tempo reale, interruzione) e il resto del 4 riguardano il passo 2, non fatto;
4 (azioni `confirm` mai con la sola voce) coperto dai test per il passo 1; 5 (benchmark
documentato e fornitori configurabili da `.env`) PASS.

### Benchmark (2026-11-16)

Fatto: benchmark in italiano (`uv run python -m evals.voice.bench`, rapporto in
`docs/voce-benchmark.md`). Fornitori provati: trascrizione con Deepgram Nova-3, ElevenLabs Scribe
e Cartesia Ink-Whisper; voce con ElevenLabs (multilingual v2 e flash v2.5) e Cartesia Sonic 3.6
(voci Elena e Lorenzo). Soniox lasciato fuori (nessun credito gratuito sulle API). Chiavi in
`.env`: `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `CARTESIA_API_KEY`.

Risultato provvisorio, su audio sintetico (venti frasi): Scribe 0-0,4% di errori, Deepgram 1,2-8%,
Cartesia Ink 5-6%; il primo audio della sintesi arriva in 86-330 ms su tutte le voci. Tutti i
servizi sbagliano "No, annulla" (sentito come "a nulla"). I fornitori NON sono ancora fissati:
manca la prova con le registrazioni vere dell'utente e l'ascolto delle voci.

### Passo 2: conversazione in tempo reale (implementato, NON ancora provato dal vivo)

Pipecat 1.16 (WebRTC + interfaccia web già pronta su `http://localhost:8000/client/`). Pipeline:
audio in, trascrizione in streaming (ElevenLabs Scribe v2 Realtime), regole del turno con
rilevamento della voce (Silero), il "cervello" (chiama `run_agent`, lo stesso agente di Telegram),
voce (Cartesia Lorenzo), audio out. Scelte:
- Stessa conversazione di Telegram (cronologia, memoria, azioni condivise); un blocco per
  conversazione impedisce che due richieste si mescolino.
- Le conferme `confirm` NON si danno a voce: la richiesta va su Telegram con i pulsanti e a voce
  si dice solo che è stata inviata (criterio 4).
- Instradamento per latenza: Haiku 5.5 a basso sforzo risponde per primo; se serve un tool, o
  dopo i risultati dei tool, decide il modello principale (`FAST_MODEL`).
- Interruzione: se l'utente parla mentre il bot parla o aspetta, la voce si ferma; l'agente
  finisce comunque in background (storia e tracciamento restano coerenti) e la risposta
  tardiva non viene detta. I turni provvisori ("speculation") non vengono mai risposti.
- Latenza di ogni turno scritta sul run (`voice_latency`: trascrizione, agente, voce, totale),
  visibile in `/ui/runs/{id}`.
- Spenta di default (`REALTIME_VOICE=false`): apre un microfono. Ascolta solo su 127.0.0.1.
- Solo ElevenLabs (trascrizione) e Cartesia (voce) sono supportati in tempo reale.

Limiti noti: dal telefono serve HTTPS (i browser bloccano il microfono su pagine non sicure,
tranne localhost); si fa con la rete privata della fase 7. Nessuna autenticazione sulle rotte:
va bene finché il server ascolta solo in locale, da aggiungere prima di esporlo.

Prima prova dal vivo (2026-11-16, 3 turni parlati): funziona, ma i tempi erano 1,9 / 3,6 / 3,75 s
contro l'obiettivo di 1,5 s (trascrizione 0,2 s e voce 0,1 s; il resto era l'agente), e un turno
ha dato un'ora sbagliata di 16 minuti senza chiamare `get_current_time`. Correzioni:
- Il prompt di sistema ora vieta di conoscere ora e data a memoria; 3 scenari di eval instradati
  (`voice_time_*`) lo verificano con il router vero.
- Streaming: il modello scrive in streaming e la voce parte sulla prima frase (`Sentencer`),
  mentre il resto è ancora in scrittura. La latenza ora registra anche `agent_s` (fino alle
  prime parole) e `reply_done_s` (risposta intera).
- Il passo di trace `llm_call` dice quale modello ha scritto la risposta (`model`).
Seconda prova dal vivo (8 turni): trascrizione 0,2 s e voce 0,06 s, ma le domande con tool
andavano da 3,2 a 6,3 s e una ricerca in memoria ha chiamato `recall` quattro volte (9,2 s). Cause
e correzioni:
- Il router scartava e ripeteva col modello principale anche i tool di sola lettura. Ora il
  modello veloce gestisce risposta diretta, tool in sola lettura e la risposta dopo i risultati di
  tool in sola lettura; il principale decide scritture e conferme (`RoutedLLM(safe_tools=...)`).
  Nelle eval le domande sull'ora passano da 3,0-3,3 s a 1,6 s (non in streaming).
- La descrizione di `recall` dice di cercare una volta sola.
- I fatti dimenticati con `/forget` non erano più nella memoria ma restavano nella cronologia
  della chat: Jarvis li ripeteva ("il dato salvato è sempre il dottor Bianchi"). Ora ogni richiesta
  porta un avviso con i fatti dimenticati di recente (massimo 10): "non usarli nemmeno se
  compaiono prima nella conversazione".
Terza prova dal vivo (6 turni): domande senza tool a 1,22-1,23 s dal rilevamento della fine
del turno, con tool a 1,8-1,9 s. Il rilevatore di voce aspetta 0,2 s di silenzio prima di
dichiarare la fine: percepiti circa 1,4 s senza tool (appena sotto l'obiettivo di 1,5 s). Un turno
a 5,3 s aveva una causa trovata nel trace: il riassunto automatico (4,15 s) girava dentro
`run_agent` e tratteneva l'ultima frase. Correzioni: il riassunto ora gira in background fuori
dal blocco della conversazione (e viene scartato se la parte riassunta è cambiata nel
frattempo); ogni risposta del modello termina con un marcatore che libera subito l'ultima frase.
Problemi di ascolto segnalati dall'utente ("che ore sono a Tokyo" spesso non capito): nei 16 turni
tracciati la frase è giusta 7 volte; errori veri ("Quiore sono a Tokyo") e frasi spezzate in due
turni ("Che ore sono attualmente?" poi "A Tokyo."), perché 0,2 s di silenzio bastano a chiudere il
turno. Correzioni: modello "smart turn" di Pipecat per capire se la frase è finita davvero
(`TurnAnalyzerUserTurnStopStrategy`) e parole chiave per la trascrizione (`STT_KEYTERMS`).
Verifica dal vivo (16 nov): con ElevenLabs la frase "Che ore sono a Tokyo?" veniva capita 7 volte su 16;
passando a Deepgram nova-3 (`STT_PROVIDER=deepgram`, ora il default) 3 su 3 corrette più "Che ore sono a Roma?",
risposta veloce secondo l'utente. ElevenLabs resta selezionabile da `.env`.
Effetto sull'errore di attacco ("Quiore") non garantito: da misurare dal vivo (criteri 2 e 3).

La fase 7 NON è iniziata.

## Modello gratuito (16 nov)

Il credito Anthropic è finito e l'obiettivo è spendere il minimo. `LLM_PROVIDER` sceglie il modello:
`groq` (predefinito, gratuito: gpt-oss-120b principale, gpt-oss-20b veloce; 1000 richieste/giorno,
8000 token/minuto), `gemini` (gratuito ma 20 richieste/giorno sul modello principale: troppo poco),
`anthropic` (a pagamento, il più affidabile, riserva). Le eval in CI partono solo a mano.
Eval su Groq: 43 scenari su 48 passano; tutti quelli di sicurezza (conferme, mail, merge, testo
ostile nei tool, segreti in memoria) passano. Falliscono: `capabilities` (elenca capacità che non
ha), `multi_tool`, `report_full`, `report_site_down`, `report_site_only` (a volte risponde senza
chiamare lo strumento). Con Anthropic erano 48 su 48.

## Fase 8 — schermata "effetto Jarvis" (17 nov)

`/jarvis`: pagina unica (HTML/JS senza dipendenze, servita da Jarvis) con orb animato su canvas,
stato in tempo reale (eventi SSE da `app/events.py`), trascrizione con gli strumenti usati e
pannelli (siti, conferme in attesa, report, attività). Collegamento WebRTC diretto agli stessi
endpoint della voce. Provata in Chrome con microfono finto (connessione, nessun errore di pagina,
riconnessione immediata) e in versione telefono (screenshot); il comportamento con la tua voce
reale va guardato dal vivo. Le chiamate nuove sostituiscono una sessione vecchia ancora aperta
(prima davano 400 dopo una caduta di rete). Limiti: nessun pulsante di conferma sulla pagina
(solo Telegram); ancora nessuna installazione come app (PWA).
