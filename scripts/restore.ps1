# Restore a backup. By default into a scratch database `jarvis_restore_test` (safe check);
# use -Into jarvis to overwrite the real database (stop the app first).
param([Parameter(Mandatory)][string]$File, [string]$Into = "jarvis_restore_test")
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."
$leaf = Split-Path $File -Leaf
docker compose cp $File "db:/tmp/$leaf"
if ($Into -ne "jarvis") {
    docker compose exec -T db psql -U jarvis -d postgres -c "DROP DATABASE IF EXISTS $Into"
    docker compose exec -T db psql -U jarvis -d postgres -c "CREATE DATABASE $Into"
}
docker compose exec -T db pg_restore -U jarvis -d $Into --clean --if-exists --no-owner /tmp/$leaf
docker compose exec -T db rm -f /tmp/$leaf
Write-Output "restored $leaf into $Into"
