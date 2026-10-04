# Brings up the harness proxy and the divert NAT (HANDOFF.md "Operate"), skipping either if it
# is already up. Run it NON-elevated: the proxy runs as the user; the NAT step asks for UAC once.
#   -RestartProxy  stop the running proxy first (disconnects the client; TerminateProcess can lose
#                  the last <=0.25 s of memory-store events, docs/NOTES.md)
#   -RestartNat    restart divert_nat even if it is running (a live client connection will drop)
# Proxy runs in its own console window (Ctrl-C there = clean shutdown with store flush).
# NAT runs in a minimized elevated window; closing that window stops the NAT.
param(
    [switch]$RestartProxy,
    [switch]$RestartNat,
    [switch]$NatWorker,  # internal: elevated NAT step
    [string]$Python      # default: $env:UO_PY, else the first python.exe on PATH that isn't the Store stub
)
$ErrorActionPreference = 'Stop'
if (-not $Python) { $Python = $env:UO_PY }
if (-not $Python) {
    $Python = (Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' } | Select-Object -First 1).Source
}
if (-not $Python) { throw 'python.exe (3.13) not found on PATH; set UO_PY' }
$Root = $PSScriptRoot
$DivertLog = Join-Path $Root 'divert.log'

function Get-DivertNat {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'divert_nat' }
}

if ($NatWorker) {
    # Elevated: only here are elevated processes' command lines readable.
    if ((Get-DivertNat) -and -not $RestartNat) { exit 3 }
    & (Join-Path $Root 'restart_divert.ps1') -Python $Python   # blocks while divert_nat runs
    exit 1
}

function Get-ProxyListener {
    Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 2593 -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

# --- proxy -------------------------------------------------------------------------------------
$listener = Get-ProxyListener
if ($listener -and $RestartProxy) {
    Write-Output "stopping proxy (pid $($listener.OwningProcess))"
    Stop-Process -Id $listener.OwningProcess -Force
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-ProxyListener) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 200 }
    if (Get-ProxyListener) { throw 'proxy port 2593 still bound after stop' }
    $listener = $null
}
if ($listener) {
    Write-Output "proxy already listening on 127.0.0.1:2593 (pid $($listener.OwningProcess))"
} else {
    $proxy = Start-Process -FilePath $Python -WorkingDirectory $Root -PassThru -ArgumentList @(
        '-u', 'harness/proxy.py',
        '--nat-lookup-port', '25943',
        '--upstream-bind', '0.0.0.0', '--upstream-bind-port', '25940',
        '--logdir', 'logs',
        '--memory-db', 'harness/data/harness.db')
    # 127.0.0.1:2593 is bound last (after control 25941 and state 25942), so it means ready.
    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Get-ProxyListener)) {
        if ($proxy.HasExited) { throw "proxy exited during startup (code $($proxy.ExitCode)); see its window" }
        if ((Get-Date) -gt $deadline) { throw 'proxy not listening on 2593 after 60 s' }
        Start-Sleep -Milliseconds 250
    }
    Write-Output "proxy up (pid $($proxy.Id)): game 127.0.0.1:2593, control 25941, state 25942"
}

# --- divert NAT (elevated) ---------------------------------------------------------------------
$t0 = Get-Date
$hostExe = (Get-Process -Id $PID).Path
$workerArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-NatWorker',
    '-Python', "`"$Python`"")
if ($RestartNat) { $workerArgs += '-RestartNat' }
try {
    $nat = Start-Process -FilePath $hostExe -ArgumentList $workerArgs -Verb RunAs `
        -WindowStyle Minimized -PassThru
} catch {
    throw "NAT not started (UAC declined?): $($_.Exception.Message)"
}
$null = $nat.Handle   # cache the handle so ExitCode stays readable after exit
$deadline = (Get-Date).AddSeconds(30)
while ($true) {
    if ($nat.HasExited) {
        if ($nat.ExitCode -eq 3) { Write-Output 'divert NAT already running'; break }
        Get-Content $DivertLog -Tail 20 -ErrorAction SilentlyContinue
        throw "divert NAT exited (code $($nat.ExitCode)); log above ($DivertLog)"
    }
    $log = Get-Item $DivertLog -ErrorAction SilentlyContinue
    if ($log -and $log.LastWriteTime -ge $t0 -and (Select-String -Path $DivertLog -Pattern 'NAT active' -Quiet)) {
        Write-Output "divert NAT up (log $DivertLog)"
        break
    }
    if ((Get-Date) -gt $deadline) { throw "divert NAT not active after 30 s; check $DivertLog" }
    Start-Sleep -Milliseconds 250
}
Write-Output 'next: powershell -Verb RunAs launch_game.ps1'
