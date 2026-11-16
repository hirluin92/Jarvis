# Daily backup of Jarvis' Postgres. Keeps the last $Keep dumps in .\backups
param([int]$Keep = 14, [string]$Dir = "$PSScriptRoot\..\backups")
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
New-Item -ItemType Directory -Force $Dir | Out-Null
$name = "jarvis-{0:yyyyMMdd-HHmmss}.dump" -f (Get-Date)
# dump inside the container and copy it out: PowerShell would corrupt a binary redirect
docker compose exec -T db pg_dump -U jarvis -d jarvis -Fc -f /tmp/$name
if ($LASTEXITCODE -ne 0) { throw "pg_dump failed" }
docker compose cp "db:/tmp/$name" "$Dir\$name"
docker compose exec -T db rm -f /tmp/$name
if ((Get-Item "$Dir\$name").Length -lt 1000) { throw "backup looks empty: $name" }
Get-ChildItem $Dir -Filter "jarvis-*.dump" | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $Keep | Remove-Item
Write-Output "ok $Dir\$name"
