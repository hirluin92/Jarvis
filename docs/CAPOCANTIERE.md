# Fase 10 — Capocantiere: come si usa

Jarvis scrive la specifica e la fa approvare su Telegram; un **processo separato** sul PC (il
lavoratore) fa lavorare Claude Code su una copia isolata del repo e apre una pull request **in bozza**.
Jarvis non lancia mai comandi: scrive solo righe nella tabella `build_jobs`.

## Cosa serve (una volta)
1. Il **clone locale** del repo del prodotto sul PC (con `origin` configurato e accesso in lettura).
2. **Claude Code** installato e con accesso attivo (comando `claude` funzionante). Usa il tuo accesso:
   i lavori consumano la tua quota di Claude Code.
3. Un **token GitHub dedicato** ai lavori, limitato ai repo abilitati, con permessi *Contents:
   read and write* e *Pull requests: read and write*. Va in `.env` come `BUILD_GITHUB_TOKEN`
   (non è `GH_WRITE_TOKEN`: il lavoratore non lo vede e Claude Code non vede nessuno dei due).
4. Un **repo di prova** per il primo lavoro vero.

## Abilitare un prodotto (`config/products.yaml`)
```yaml
  - name: QuadroRW
    github_repo: utente/quadrorw
    local_path: C:/Progetti/quadrorw       # clone locale
    build_enabled: true                    # senza questo il prodotto è rifiutato
    test_command: pytest -q                # i test, eseguiti dal lavoratore dopo Claude Code
    protected_paths:                       # un lavoro che li tocca fallisce prima del push
      - src/orders/**
      - "*.pem"
```
Per IMS i moduli che eseguono ordini vanno in `protected_paths`.

## Avviare il lavoratore
`uv run python -m worker` (finestra a parte, o attività pianificata all'accesso). Impostazioni in
`.env`: `BUILD_MAX_PARALLEL` (1), `BUILD_TIMEOUT_MINUTES` (45), `BUILD_MAX_TURNS` (80),
`BUILD_MODEL` (vuoto = predefinito di Claude Code), `BUILD_MAX_BUDGET_USD` (0 = nessun tetto),
`BUILD_WORKDIR` (`build-work`).

## Flusso
«Su QuadroRW aggiungi l'esportazione in PDF» → (al massimo tre domande se è ambigua) → specifica →
su Telegram **la specifica intera** con Conferma/Annulla → lavoro in coda → ramo `jarvis/<id>-<titolo>`
→ test → push del ramo → PR in bozza → messaggio su Telegram con link, riassunto, esito dei test.
Il merge resta `merge_pull_request`, con conferma.

Comandi: `/builds` (ultimi lavori), `/build <id>` (dettaglio), `/resume <id>` (riprende un lavoro
in pausa per quota). Si ferma un lavoro a parole («ferma il lavoro 4», `cancel_build`).

## Come è tenuto sicuro
- Claude Code parte con un ambiente **pulito** (solo PATH e cartelle dell'utente per trovare il suo
  accesso): nessuna variabile di Jarvis, nessun token, nessuna chiave.
- Opzioni verificate su Claude Code 2.1.326: `--restricted` (ignora impostazioni e hook del repo,
  file solo nella cartella di lavoro), `--permission-mode dontAsk` + `--permission-prompts none`
  (ciò che non è esplicitamente permesso è negato, niente attese), strumenti consentiti: leggere,
  modificare, scrivere, cercare, `git status`, `git diff` e **solo** il `test_command`. Non può usare
  rete, `git push`, installazioni. **`--max-turns` non esiste in questa versione**: i passaggi si
  contano dal flusso di eventi e il processo viene fermato oltre `BUILD_MAX_TURNS`.
- Il lavoratore spinge **solo** rami `jarvis/...`, mai il principale; non fa merge né deploy.
- File compilati e di scarto (`__pycache__`, `node_modules`, ...) non entrano mai nella commit.
- Il contenuto del repo è dato: il prompt lo dice e le eval verificano che un README ostile non
  faccia partire nulla.

## Limiti
- I lavori usano la quota di Claude Code: a quota finita il lavoro va in `paused_quota` e si
  riprende con `/resume`.
- Claude Code non può installare dipendenze né usare la rete: modifiche che le richiedono falliscono.
- Un solo lavoro alla volta (default). I lavori non sopravvivono a un riavvio del PC: all'avvio il
  lavoratore segna come falliti quelli rimasti «in corso».
