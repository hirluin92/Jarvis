# Fase 12 — Cacciatore di nicchie: come si usa

Jarvis cerca problemi reali che la gente descrive, li salva **con la prova** (link e citazione), li
valuta e ti porta le migliori; tu decidi a ogni cancello. Non garantisce il successo di nessun prodotto.

## Cosa serve
- Un **modello capace** per il radar (`ADVISOR_PROVIDER`, `ADVISOR_MODEL`, `ADVISOR_DAILY_BUDGET`): il
  radar usa il modello del consigliere e il suo tetto di spesa. Con il modello gratuito funziona ma con
  ricerche più povere.
- `TAVILY_API_KEY` (già configurata) per la ricerca web.
- Facoltativi in `.env`: `RADAR_DAY` (mon), `RADAR_TIME` (09:00), `RADAR_TOPICS` (settori separati da
  virgola; vuoto = ricerca aperta), `SCORE_WEIGHTS` (JSON per cambiare i pesi).

## Come si usa
- Ogni settimana (o con `/radar`) Jarvis cerca nicchie e ti manda fino a **tre opportunità**, ognuna in un
  messaggio con problema, punteggio, criteri sconosciuti, prove con link e citazione, e tre pulsanti:
  **Approva verifica**, **Scarta**, **Più dati**.
- `/nicchie` elenca le opportunità; `/consiglio Nicchie` apre la conversazione da consigliere su di esse.
- **Approva verifica**: Jarvis scrive il *kit di verifica* (pagina, modulo con consenso, offerta, dieci domande
  per le interviste, bozze dei messaggi) con le **soglie numeriche**, la **scadenza** e i **criteri di
  chiusura scritti prima**. Ti arriva intero su Telegram con Conferma/Annulla.
- A scadenza porti i numeri («41 iscritti su 30»): Jarvis propone *costruire / non costruire / chiudere*
  (`decide_opportunity`, con conferma, che mostra se le soglie sono state raggiunte). «Costruire» non avvia
  nulla: se l'opportunità ha un prodotto nel catalogo scrive una **bozza** di specifica per il capocantiere
  (che poi chiede la sua conferma); altrimenti ti dice di aggiungere il prodotto a `config/products.yaml`.

## Le regole che non si possono aggirare (sono nel codice)
- Una prova ha un indirizzo web vero e la citazione letta: senza, viene rifiutata.
- Un voto da 0 a 5 richiede il motivo e gli id delle prove **di quella opportunità**; se mancano, il criterio
  resta **sconosciuto** (non vale zero) e nessun numero viene inventato.
- La scheda mostrata dal radar è costruita dal database, non dalle parole del modello.
- Una prova con più di un anno è segnata `[VECCHIA]`. Il radar mostra solo opportunità con almeno 3 prove.
- Il kit senza soglie, scadenza futura e criteri di chiusura viene rifiutato.
- Decidere e avviare la verifica sono azioni **a conferma su Telegram**. Le pagine lette sono dati, non istruzioni.

## Limiti
- Niente nicchie che muovono denaro di terzi, fanno trading o danno consulenza regolata; niente recensioni
  o testimonianze false.
- Il radar legge solo pagine pubbliche, rispetta `robots.txt` e i termini dei siti, non usa pagine dietro login.
- La raccolta delle iscrizioni e il contatto con le persone sono la **fase 13**.
