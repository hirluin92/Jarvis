# Fase 13 — Clienti: come si usa

Jarvis vede chi risponde, tiene l'elenco dei contatti con la fonte e la base giuridica, prepara i messaggi
e li manda **solo con il tuo clic** (o dentro un'autonomia con limiti scritti). Non è consulenza legale.

## Cosa serve (una volta)
1. **Un'etichetta Gmail** (predefinita `jarvis-clienti`, `INBOX_LABEL`) da assegnare ai messaggi che Jarvis può
   leggere, meglio ancora una **casella dedicata** al prodotto. Jarvis legge **solo** i messaggi con quell'etichetta.
2. **Il permesso di lettura**: rilancia `uv run python -m app.auth_gmail` (ora chiede anche `gmail.readonly`).
   Nota onesta: Google non ha un permesso di lettura "per etichetta", quindi il permesso copre l'intera casella e il
   limite all'etichetta è **nel codice** (un messaggio senza etichetta non viene mai scaricato né salvato).
   La casella dedicata è l'opzione più rigida.
3. In `.env`: `OUTREACH_SENDER_NAME` (il tuo nome: senza, **nessun invio**), `OUTREACH_DAILY_CAP` (20),
   `REPLY_DAILY_CAP` (10), `OUTREACH_HOURS` (`9-18`), `OUTREACH_DIGEST_TIME` (`09:30`).
4. **Prima del primo contatto a freddo a un'azienda** fai verificare a un professionista la base giuridica.

## Come funziona
- **Posta in arrivo**: ogni 10 minuti Jarvis legge i messaggi nuovi dell'etichetta. Una richiesta di non essere
  più contattati («cancellami», «STOP», «non scrivermi più») o un rimbalzo sono gestiti da **regole**, prima che
  un modello veda il testo: l'indirizzo va nell'elenco di esclusione per sempre. Gli altri messaggi sono classificati
  e riassunti da un modello **senza alcuno strumento** (il testo ricevuto è un dato ostile, non un'istruzione) e ti
  arrivano su Telegram con la risposta proposta e i pulsanti **Rispondi / Ignora**.
- **Contatti**: `add_lead` richiede **fonte pubblica e base giuridica**; gli indirizzi personali (gmail, libero,
  hotmail...) sono rifiutati per i contatti a freddo. Il modello può registrare solo contatti **aziendali** o persone
  che hanno **davvero scritto** (verificato dai messaggi ricevuti). Il **consenso esplicito** lo dichiari solo tu:
  `/iscritto <email> <fonte> <come ha dato il consenso>`.
- **Coda di approvazione**: `queue_outreach` mette il messaggio in coda e **non invia nulla**. Ogni mattina (o con
  `/coda`) ricevi i messaggi del giorno, ognuno con fonte, base e testo, e i pulsanti **Invia / Salta / Modifica**.
  Il clic su **Invia** è la conferma. Per cambiare il testo: `/modifica <id> <nuovo testo>`.
- **Ogni messaggio** esce dalla tua casella con la tua firma, l'avviso che è scritto con l'aiuto di un assistente AI
  e il modo per non riceverne altri (rispondere STOP).
- **Numeri**: `/numero <voce> <valore> <fonte>` (per esempio `/numero iscrizioni 12 modulo`). Un numero senza fonte non
  si salva. Il riesame settimanale li usa citando la fonte di ciascuno.

## I limiti, scritti nel codice
- **Un solo contatto a freddo** per persona, al massimo **2 seguiti**, a **3 giorni** di distanza.
- **Tetto giornaliero** di contatti a freddo e **orari** di invio; fuori tetto o orario il messaggio resta in attesa.
- `/stop` ferma **ogni** invio e la coda; solo tu con `/riprendi` lo ripristini.
- **Livelli** (`/livello 0|1|2`, solo tu, **nessuno strumento del modello può cambiarli**): 0 solo bozze; 1 ogni invio
  con il tuo clic (predefinito); 2 invio autonomo **solo** per risposte a chi ha scritto per primo e seguiti a chi ha
  dato il consenso, con tetto giornaliero, orari, testo senza link e non oltre 800 caratteri. Mai a freddo.
- Troppi reclami o rimbalzi (2, o il 5% degli invii) fanno **tornare il livello 2 al livello 1** e ti avvisano.
- `forget_lead` cancella un contatto e i suoi messaggi in coda e mette l'indirizzo nell'elenco di esclusione per sempre.

## Cosa non c'è (apposta o ancora)
- **Niente automazione dei social** (LinkedIn, Reddit, X, Instagram): vietata dai termini, fa chiudere gli account.
  I testi per i social sono solo bozze da pubblicare a mano.
- **Niente spese o annunci in autonomia**: gli annunci sono solo proposte. `GROWTH_MONTHLY_BUDGET` è il tetto di
  qualsiasi spesa futura, sempre con conferma.
- **Non c'è ancora** la raccolta delle iscrizioni da un modulo sul sito: per ora si registrano a mano con `/iscritto`.
  Né la chat sul sito.
- Per il livello 2 la specifica prevedeva «modelli di testo approvati»: al loro posto ci sono i controlli sopra
  (nessun link, lunghezza, tetto, orari, blocco automatico). Se vuoi anche i modelli, è un'aggiunta da fare.
