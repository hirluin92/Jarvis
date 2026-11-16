#!/bin/sh
# Daily backup for a Linux server (cron): keeps the last 14 dumps in ./backups
set -eu
cd "$(dirname "$0")/.."
mkdir -p backups
name="jarvis-$(date +%Y%m%d-%H%M%S).dump"
docker compose exec -T db pg_dump -U jarvis -d jarvis -Fc > "backups/$name"
[ "$(wc -c < "backups/$name")" -gt 1000 ] || { echo "backup looks empty" >&2; exit 1; }
ls -1t backups/jarvis-*.dump | tail -n +15 | xargs -r rm --
echo "ok backups/$name"
