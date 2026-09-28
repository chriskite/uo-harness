# Points the Outlands client's settings.json at the harness proxy (127.0.0.1:2593).
# Keeps a one-time backup beside the original (settings.json.direct-backup).
$src = 'C:\Program Files (x86)\Ultima Online Outlands\ClassicUO\settings.json'
$bak = 'C:\Program Files (x86)\Ultima Online Outlands\ClassicUO\settings.json.direct-backup'
if (-not (Test-Path $bak)) { Copy-Item $src $bak }
$cfg = Get-Content $src -Raw | ConvertFrom-Json
$cfg.ip = '127.0.0.1'
$cfg.port = 2593
$cfg | ConvertTo-Json | Set-Content $src -Encoding UTF8
Write-Output "settings.json -> ip=127.0.0.1 port=2593 (backup: settings.json.direct-backup)"
