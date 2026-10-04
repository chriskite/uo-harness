# Monitors all TCP/UDP endpoints touched by Outlands.exe / ClassicUO.exe
# Usage: powershell -ExecutionPolicy Bypass -File monitor_endpoints.ps1 -Seconds 420
param([int]$Seconds = 420)

$out = Join-Path $PSScriptRoot 'behavioral_endpoints.csv'
$seen = @{}
$start = Get-Date
"firstSeen,lastSeen,process,localPort,remoteAddr,remotePort,state" | Out-File $out

while (((Get-Date) - $start).TotalSeconds -lt $Seconds) {
    $procs = Get-Process -Name "ClassicUO","Outlands" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        $conns = Get-NetTCPConnection -OwningProcess $p.Id -ErrorAction SilentlyContinue |
                 Where-Object { $_.RemoteAddress -notin @("0.0.0.0","::","127.0.0.1","::1") }
        foreach ($c in $conns) {
            $key = "$($p.Name)|$($c.LocalPort)|$($c.RemoteAddress)|$($c.RemotePort)"
            $now = (Get-Date).ToString("HH:mm:ss")
            if ($seen.ContainsKey($key)) {
                $seen[$key].last = $now
                $seen[$key].state = $c.State
            } else {
                $seen[$key] = [pscustomobject]@{ first=$now; last=$now; proc=$p.Name; lp=$c.LocalPort; ra=$c.RemoteAddress; rp=$c.RemotePort; state=$c.State }
                Write-Host "NEW $($p.Name) -> $($c.RemoteAddress):$($c.RemotePort) ($($c.State))"
            }
        }
    }
    Start-Sleep -Seconds 2
}
$seen.Values | ForEach-Object { "$($_.first),$($_.last),$($_.proc),$($_.lp),$($_.ra),$($_.rp),$($_.state)" } | Out-File $out -Append
Write-Host "done. $($seen.Count) distinct connections -> $out"
