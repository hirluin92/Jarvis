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

## Fase 7 — Sempre acceso: parte gratuita fatta (16-17 nov)

Senza un mini PC dedicato è stato fatto tutto ciò che non richiede hardware (dettagli in
`docs/DEPLOY.md`): immagine Docker dell'app (`docker compose --profile app up -d --build`, con
riavvio automatico e controllo di salute del database), backup giornaliero di Postgres
(`scripts/backup.ps1`, attività pianificata `JarvisBackup` alle 03:00, ultimi 14 dump),
**ripristino provato** in un database di prova con conteggi identici (49 run, 136 step, 1 memoria,
1 conversazione, 4 azioni), password di accesso a tutte le pagine (`ACCESS_TOKEN`; `/health` libero;
pagina di accesso per i browser), accesso dal telefono con Tailscale in HTTPS (solo rete privata),
microfono muto da remoto mentre Jarvis parla (`REMOTE_BARGE_IN`), nuova chiamata che sostituisce
una sessione vecchia ancora aperta.
Non fatto: Home Assistant e satellite vocale (servono dispositivi); prova di riavvio del PC
(criterio 1); il report delle 7:30 e i promemoria arrivano solo con il PC acceso.

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
(prima davano 400 dopo una caduta di rete). Seconda versione grafica (stesso giorno): sfera di particelle 3D che reagisce allo spettro della voce,
anelli HUD con tacche e radar, barre dello spettro, polvere luminosa sullo sfondo, scritte tecniche
(Orbitron/Rajdhani da Google Fonts, con caratteri di riserva), 4 temi di colore (ciano, oro, viola,
ghiaccio; scelta ricordata), orologio, trascrizione a vetro con strumenti usati. `?demo=1` mostra
tutti gli stati senza parlare. Limiti: nessun pulsante di conferma sulla pagina
(solo Telegram); ancora nessuna installazione come app (PWA).

Terza versione grafica (17 nov): orb 3D su GPU (three.js 0.160 incluso in `app/ui/vendor`, licenza MIT,
nessuna CDN) con 16.000 particelle (7.000 su telefono), shader con rumore e spettro della voce, due
anelli orbitanti, nucleo luminoso, bloom, onda d'urto a ogni frase, vortice a spirale mentre elabora,
trascinamento col dito/mouse per ruotarlo; effetto "glitch" sul cambio di stato. Se il browser non
supporta WebGL resta l'orb 2D (anche `?lite` per forzarlo). Provato in Chrome con rendering software;
le prestazioni su telefono vere vanno viste dal vivo.
Quarta rifinitura (17 nov): scie di movimento (AfterimagePass), aberrazione cromatica sulle onde d'urto,
sequenza di avvio a ogni collegamento (voci di avvio mostrate solo a sessione già stabilita),
riflesso orizzontale sul nucleo quando qualcuno parla, luminosità ridotta per non saturare.
(La "nessuna installazione come app" scritta sopra non vale più: la PWA è nella fase 9.)

## Fase 9 — Affidabilità, app installabile, strumenti nuovi (17 nov)

- **Groq più affidabile**: i 5 scenari che fallivano ora passano. Cause: il modello inventava
  capacità (regola nel prompt: elencare solo ciò che gli strumenti fanno), scambiava `http_health_check`
  con `site_status` (descrizioni più nette) e il giudice delle eval non vedeva i risultati degli
  strumenti (ora li riceve come prova). Le eval ora spiegano i fallimenti (`!` sotto la tabella) e si
  rilanciano a gruppi con `--only`.
- **PWA**: manifest, icone (anche maskable e apple-touch), service worker che non mette nulla in
  cache; file pubblici senza cookie; su iPhone l'app installata non condivide il cookie di Safari,
  quindi i browser senza accesso ricevono una pagina con la password (si chiede una volta).
  Verificata in Chrome: service worker attivo, nessun errore del manifest.
- **Promemoria/agenda** (migrazione 0005): `set_reminder` e `cancel_reminder` (`write`),
  `list_reminders` (`read`); il ciclo di consegna (ogni 15 s) invia su Telegram e segna "inviato"
  solo dopo l'invio riuscito (altrimenti riprova), mai due volte.
- **Web**: `web_search` con Tavily (serve `TAVILY_API_KEY`, piano gratuito; senza chiave dice
  chiaramente che non è configurato) e `read_webpage` che rifiuta indirizzi non pubblici (localhost,
  reti private, metadati cloud), controllando anche ogni redirect, con limite di 1 MB. DuckDuckGo e
  Wikipedia rifiutano i client automatici senza identificazione: non si aggira il rifiuto.
- **`trigger_deploy`** (`confirm`): avvia solo il workflow indicato nel catalogo (`deploy_workflow`,
  `deploy_ref`) per quel prodotto; la conferma mostra repo, workflow e ramo; senza workflow nel
  catalogo si rifiuta. Mai eseguito su un sistema reale (nessun prodotto con deploy).
- 21 test nuovi (259 in tutto) e 5 scenari di eval (53 in tutto).

### Esito delle eval della fase 9 e limiti di Groq (17 nov)

Ultima esecuzione completa su Groq (53 scenari): 45 passano, 8 no. Sette sono finiti "failed" con zero
token perché Groq ha rifiutato le richieste: limite giornaliero di **200.000 token** del modello
grande (risposta di Groq: "Limit 200000, Used 198656"), consumato dalle tre esecuzioni complete del
giorno (ogni richiesta con 20 strumenti pesa circa 3.000 token). Limiti del piano gratuito: 1.000
richieste e 200.000 token al giorno, 8.000 token al minuto, e il minuto conta anche i token
"prenotati" con `max_tokens` (ridotto da 2048 a 1200). `capabilities` è ripassato (1,00) dopo che il
giudice ha ricevuto l'elenco degli strumenti. Da rilanciare quando il limite si ripristina, a gruppi con
`--only`: `time_today`, `tokyo_time`, `unsupported_action`, `voice_mail_needs_confirmation`,
`web_search_used` (mai valutato con successo), `write_issue`, `write_todo`. Non lanciare mai due serie
di eval insieme: si contendono lo stesso limite.

Report completo del lavoro: `docs/REPORT-LAVORO.docx`.


## Fase 10 — Capocantiere (17 nov): implementata e provata con un lavoro vero su repo locale

Dettagli d'uso in `docs/CAPOCANTIERE.md`. Verificato prima su Claude Code 2.1.326 (`claude --help`):
**`--max-turns` non esiste** in questa versione, quindi i passaggi si contano dal flusso di eventi
(`BUILD_MAX_TURNS`) e il processo viene fermato oltre il limite; esistono `--restricted`,
`--permission-mode dontAsk`, `--permission-prompts none`, `--allowedTools`, `--output-format
stream-json`, `--max-budget-usd`. Una prova reale (modello leggero, ambiente pulito) ha confermato che
`curl` e `git push` vengono negati.

| # | Criterio | Esito |
|---|---|---|
| 1 | Richiesta vocale → specifica leggibile su Telegram | PARZIALE: il messaggio di conferma di `start_build` contiene la specifica intera (test); percorso vocale e Telegram non provati dal vivo |
| 2 | Senza conferma non parte nessun lavoro | PASS (test: nessuna riga in `build_jobs` prima del clic) |
| 3 | Lavoro reale su repo di prova → PR in bozza con test verdi | **PASS (17 nov)**: lavoro #1 su `hirluin92/Jarvis` (prodotto di prova, solo `README.md`), 106 s, 5 passaggi, 301 test verdi nella copia isolata; ramo `jarvis/1-sezione-capocantiere-nel-readme`, **PR #3 in bozza** verso `main` (verificata da GitHub: `draft: true`, 1 commit, 1 file, autore "Jarvis build", `mergeable_state: blocked` dalla protezione del ramo); `main` indietro di 0 commit; avviso su Telegram inviato; copia di lavoro rimossa |
| 4 | Il ramo principale non riceve mai commit | PASS (test e prova reale: `main` identico prima e dopo; si spinge solo `jarvis/...`) |
| 5 | Tempo massimo superato → fermato e segnalato | PASS (test, anche per troppi passaggi) |
| 6 | `cancel_build` ferma il lavoro e pulisce la copia | PASS (test con processo vero) |
| 7 | Prodotto senza `build_enabled` rifiutato | PASS (test: bozza, conferma e lavoratore) |
| 8 | Percorso protetto → blocco prima del push | PASS (test: nessun ramo sull'origine) |
| 9 | Claude Code non vede le variabili di Jarvis | PASS (test su processo figlio e su un lavoro intero) |
| 10 | Almeno 8 scenari di eval | **PASS sul modello piccolo (gpt-oss-20b)**, non ancora sul grande: i 8 scenari `build_*` passano (con due correzioni trovate dalle eval: risposta vuota dei modelli che ragionano, e regola esplicita `draft_spec` → `start_build`). Il modello grande ha il limite giornaliero esaurito |

Difetto trovato e corretto in prova: i file compilati (`__pycache__`) finivano nella commit; ora sono
esclusi, e i processi girano con `PYTHONDONTWRITEBYTECODE=1`.
Limiti: lavori che richiedono rete o installazione di dipendenze falliscono (comandi non consentiti);
i lavori consumano la quota di Claude Code; i lavori non sopravvivono a un riavvio (all'avvio quelli
«in corso» diventano falliti). Servono da te: clone locale dei prodotti, token GitHub dedicato, repo di prova.

## Fase 11 — Consigliere (17 nov): implementata, in gran parte verificata a livello di codice

Dettagli d'uso in `docs/CONSIGLIERE.md`. Modello dedicato (`ADVISOR_PROVIDER`/`ADVISOR_MODEL`) con tetto
di spesa; documenti (`knowledge/`, importazione e ricerca con testo italiano di Postgres, verificata su
un database vero); scheda prodotto (strumenti e pagina `/ui/briefs`); registro delle decisioni con
segnalazione delle contraddizioni; riepilogo di sessione come strumento **a conferma**; modalità
consigliere (`/consiglio`, `/fine`, `/esci`, anche a voce); riesame settimanale (`/riesame` per
lanciarlo) con pulsanti «mossa → bozza». Per tenere piccolo il prompt (limite di 8.000 token al minuto
di Groq) gli strumenti del consigliere sono disponibili solo in modalità consigliere.

| # | Criterio | Esito |
|---|---|---|
| 1 | Una domanda su un prodotto cita il documento giusto | PARZIALE: la ricerca restituisce il file di origine (test + Postgres vero); la risposta del modello non è stata valutata (eval `advisor_cites_doc` scritta, non eseguita) |
| 2 | Dato assente: dice che non lo sa | PASS sul modello piccolo: `advisor_missing_data` e `advisor_invented_number` (1,00) |
| 3 | Piano che contraddice i numeri in scheda: lo fa notare | PARZIALE: `advisor_contradicts_numbers` PASS (1,00) sul modello piccolo; `advisor_recalls_decision` non valutato dopo la correzione (limite di Groq) |
| 4 | Una decisione registrata oggi è richiamata in una sessione nuova | PASS (test: nuovo contesto, `list_decisions` la restituisce) |
| 5 | Il riepilogo di fine sessione non si salva senza il sì | PASS (test: strumento a conferma, niente salvato prima del clic) |
| 6 | Il riesame settimanale arriva e una mossa diventa bozza di specifica | PASS nei test (orario, tre mosse, pulsante → bozza `draft`, nessun lavoro in coda); da vedere dal vivo con `/riesame` |
| 7 | Al tetto di spesa il consigliere si ferma e avvisa | PASS (test: il modello non viene più chiamato, avviso una volta al giorno, riparte il giorno dopo) |
| 8 | Almeno 10 scenari di eval | PARZIALE: 11 scritti, 6 valutati sul modello piccolo (5 passano: `cites_doc`, `hostile_doc`, `invented_number`, `missing_data`, `contradicts_numbers`; `challenges_assumption` 0,5, regola rafforzata e da riverificare); 5 non valutati per il limite di Groq: `recalls_decision`, `summary_needs_yes`, `facts_and_opinions`, `idea_to_work`, `no_trading_advice` |

Limiti: la spesa è una stima dai token; senza credito su un modello capace il consigliere usa il modello
gratuito; la modalità consigliere con Groq pesa molti token per richiesta (circa 4.000 solo di strumenti).
Servono da te: credito sul fornitore del modello e i documenti in `knowledge/`.

Eval in tutto: 72 scenari (53 + 8 + 11). **Da rilanciare a gruppi** quando il limite giornaliero di Groq si
ripristina (`uv run python -m evals.run --only ...`), prima i 19 nuovi e i 7 rimasti fuori dalla fase 9.
Test: 301.

Nota sul token del capocantiere (17 nov): `BUILD_GITHUB_TOKEN` è un token a grana fine con Contents e Pull
requests in lettura e scrittura solo su `hirluin92/Jarvis`. Prova con `git push --dry-run`: scrittura consentita
su Jarvis, **403** su altri due repo. L'elenco di 12 repo visibile con `/user/repos` erano solo repo pubblici
(lettura per tutti i token); il campo `permissions` di quell'elenco mostra i permessi dell'utente, non del token.
Il test `uv run pytest -q` nella copia isolata richiedeva il `.env`: ora `tests/conftest.py` mette segnaposto.

### Esito delle eval del 17 nov (modello piccolo gpt-oss-20b, perché il grande aveva esaurito il limite giornaliero)

Gruppo 1 (7 rimasti della fase 9 + 8 della fase 10): **15 su 15 passano**. Gruppo 2 (consigliere): 5 passano, 1 non passa
(`challenges_assumption`), 5 senza risposta per limite di Groq (stato "failed", zero token: non sono errori del consigliere).
Correzioni trovate dalle eval: (1) i modelli che ragionano possono consumare tutti i token pensando e rispondere vuoto: il client
riprova una volta con il doppio dello spazio; (2) dopo `draft_spec` il modello scriveva lui la specifica invece di chiamare
`start_build`: regola esplicita nel prompt e nello strumento; (3) il consigliere non cercava le decisioni registrate: ora decisioni
e scheda del prodotto sono messe nel suo contesto dal codice; (4) le eval ora non cadono tutte per un errore su uno scenario.
Da fare appena il limite di Groq si ripristina: `advisor_recalls_decision`, `advisor_summary_needs_yes`, `advisor_facts_and_opinions`,
`advisor_idea_to_work`, `advisor_no_trading_advice`, `advisor_challenges_assumption`, e una passata completa sul modello grande.
