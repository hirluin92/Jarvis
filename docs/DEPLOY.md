# Fase 7 — Jarvis sempre acceso (senza spese)

Tutto quello che segue è gratuito. Il server dedicato non c'è: oggi Jarvis gira sul PC di
Gianluca, ma i file sono pronti per spostarlo su un mini PC o un server Linux quando ci sarà.

## 1. Avviare Jarvis in Docker

```
docker compose --profile app up -d --build
```

Parte il database e l'app (applica le migrazioni da sola). Entrambi hanno `restart: unless-stopped`:
se Docker si riavvia, tornano su. Sul PC Windows attiva in Docker Desktop
*Settings → General → Start Docker Desktop when you sign in*.

Prima di avviare l'app in Docker ferma il server locale (`uvicorn`): due bot Telegram con lo
stesso token si disturbano. Per tornare allo sviluppo: `docker compose --profile app stop app`.

L'app ascolta solo su `127.0.0.1:8000`: nessuna porta è esposta su internet.

## 2. Backup e ripristino

- `scripts/backup.ps1` salva un dump in `backups/` e tiene gli ultimi 14.
  Su questo PC gira ogni giorno alle 03:00 (attività pianificata `JarvisBackup`).
  Su Linux: `scripts/backup.sh` da `cron` (`0 3 * * * /percorso/jarvis/scripts/backup.sh`).
- `scripts/restore.ps1 -File backups\jarvis-AAAAMMGG-HHMMSS.dump` ripristina in un database di
  prova (`jarvis_restore_test`). Con `-Into jarvis` sovrascrive quello vero: ferma prima l'app.
- Copia ogni tanto `backups/` fuori dal PC (una cartella sincronizzata, un disco esterno).

Provato il 16 novembre: il dump è stato ripristinato in un database nuovo e le tabelle
`runs`, `steps`, `memories`, `conversations`, `actions` avevano gli stessi conteggi dell'originale.

## 3. Password sulle pagine

Con `ACCESS_TOKEN` vuoto le pagine web (`/runs`, `/ui/...`, `/client/`) sono aperte: va bene
solo in locale. Prima di raggiungere Jarvis dal telefono imposta in `.env` un valore lungo e
casuale, per esempio con `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
La prima volta apri `https://<indirizzo>/?token=<valore>`: il browser tiene un cookie per 90 giorni.
`/health` resta libero (serve ai controlli di salute).

## 4. Dal telefono con Tailscale (rete privata)

1. Crea un account gratuito su tailscale.com e installa l'app su PC e telefono.
2. Sul PC: `tailscale serve --bg 8000` espone Jarvis in HTTPS **solo dentro la tua rete privata**
   (indirizzo tipo `https://nome-pc.tuarete.ts.net`). HTTPS serve al microfono del telefono.
3. Non usare `tailscale funnel`: quello apre a internet.
4. Imposta `ACCESS_TOKEN` (punto 3) e apri `https://nome-pc.tuarete.ts.net/client/?token=...`.

## 5. Avviso se Jarvis è giù

Servizio esterno gratuito, per esempio healthchecks.io: crea un controllo, copia l'URL di ping
e programma sul PC una chiamata ogni 5 minuti a quell'URL **solo se** `/health` risponde `ok`
(così, se Jarvis o il PC si fermano, il ping smette e il servizio ti scrive).

## Cosa non è fatto

- Home Assistant e satellite vocale: richiedono hardware e dispositivi.
- Il report delle 7:30 arriva solo con il PC acceso a quell'ora.
- Il criterio "dopo un riavvio torna su da solo" è predisposto (restart + avvio di Docker Desktop)
  ma va verificato riavviando davvero il PC.

## Schermata "effetto Jarvis" (fase 8)

Indirizzo: `/jarvis` (la home `/` porta lì). Si apre da PC (`http://localhost:8000/jarvis`) e dal
telefono (`https://<nome>.ts.net/jarvis?token=...`). Un orb animato reagisce al microfono e alla
voce di Jarvis; sotto, lo stato (in ascolto / ti sento / ci penso / parlo), la trascrizione con
gli strumenti usati, e i pannelli (siti, conferme in attesa, report del mattino, attività recente).
Le conferme si danno **solo su Telegram**: la schermata le mostra, non le approva.
Il collegamento vocale è lo stesso della fase 6 (`REALTIME_VOICE=1`).
