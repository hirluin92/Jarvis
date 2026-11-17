# Fase 11 — Consigliere: come si usa

Parli con Jarvis dei tuoi prodotti come con un socio che conosce i documenti, i numeri e le decisioni
già prese, ti contraddice quando serve e trasforma le decisioni in lavoro per il capocantiere.

## Cosa serve (una volta)
1. **Credito su un fornitore di modelli capace** e le sue impostazioni in `.env`:
   `ADVISOR_PROVIDER` (`anthropic`, `gemini` o `groq`; vuoto = lo stesso di `LLM_PROVIDER`),
   `ADVISOR_MODEL` (per esempio `claude-opus-5-5`; vuoto = il modello principale del fornitore) e
   `ADVISOR_DAILY_BUDGET` (dollari al giorno, 0 = nessun tetto). Senza queste il consigliere usa il
   modello di ogni giorno (gratuito, ma meno capace).
2. I **documenti dei prodotti in Markdown** in `knowledge/<NomeProdotto>/*.md` (il nome della cartella
   è quello del catalogo), poi `uv run python -m app.knowledge_import`. Si può rilanciare dopo ogni
   modifica: le sezioni di un file vengono sostituite.
3. La **scheda** di ogni prodotto: la compili parlando («aggiorna la scheda di QuadroRW: 12 clienti...»)
   o dalla pagina `/ui/briefs`.

## Come si usa
- `/consiglio <prodotto>` entra in modalità consigliere (anche a voce: «consigliere su QuadroRW»).
  Le risposte usano il modello dedicato. `/fine` chiude la sessione: propone il riepilogo di decisioni
  e azioni e lo salva **solo se premi Conferma** su Telegram. `/esci` torna alla conversazione normale.
- A voce risponde in due o tre frasi e manda i dettagli su Telegram.
- Distingue **Fatto** (da documento o strumento, con la fonte) da **Opinione**; non inventa numeri;
  mette in discussione le ipotesi; se il piano contraddice una cifra della scheda o una decisione
  registrata, lo dice.
- **Registro delle decisioni**: decisione, motivi, alternative scartate, data di riesame. Una decisione
  che ne ribalta una precedente viene segnalata.
- **Riesame settimanale**: ogni `REVIEW_DAY` alle `REVIEW_TIME` (default lunedì 08:00) per ogni prodotto
  numeri, lavoro fatto, decisioni da riesaminare e tre mosse con il motivo; il pulsante «Mossa N → bozza»
  crea una bozza di specifica del capocantiere (che poi si avvia con conferma). `/riesame` lo lancia subito.
- **Tetto di spesa**: raggiunto `ADVISOR_DAILY_BUDGET` il consigliere smette di chiamare il modello,
  risponde con un avviso e te lo scrive su Telegram (una volta al giorno).

## Strumenti (solo in modalità consigliere, per tenere piccolo il prompt)
`search_docs`, `get_product_brief`, `update_product_brief`, `log_decision`, `list_decisions`,
`save_session_summary` (conferma). Sempre disponibili: `set_advisor_mode`, `send_to_telegram`.

## Limiti
- Il consigliere propone, non decide: ogni azione passa dalle conferme su Telegram.
- Nessun consiglio su singole operazioni di trading o su investimenti personali.
- I documenti importati sono dati: un testo con istruzioni non cambia il comportamento.
- Ricerca testuale di Postgres in italiano (per radici di parola): «dentista» non trova «odontoiatra».
  Si passa alla ricerca semantica solo se le eval mostrano che non basta.
- La spesa è una stima dai token (prezzi in `app/costs.py`); per un modello senza prezzo noto conta 0.
