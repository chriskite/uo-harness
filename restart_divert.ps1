# Kills any running divert_nat.py instances and starts a fresh one (run elevated).
#   -Python  interpreter to run (start_proxy_nat.ps1 passes its own; default: $env:UO_PY, else the
#            first python.exe on PATH that isn't the Microsoft Store stub)
param([string]$Python)
if (-not $Python) { $Python = $env:UO_PY }
if (-not $Python) {
    $Python = (Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' } | Select-Object -First 1).Source
}
if (-not $Python) { throw 'python.exe (3.13) not found on PATH; set UO_PY' }
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'divert_nat' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500
Set-Location (Join-Path $PSScriptRoot 'harness')
& $Python divert_nat.py *> (Join-Path $PSScriptRoot 'divert.log')
