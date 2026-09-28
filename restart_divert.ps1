# Kills any running divert_nat.py instances and starts a fresh one (run elevated).
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'divert_nat' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500
Set-Location 'C:\Users\chris\uo-harness\harness'
& 'C:\Users\chris\AppData\Local\Programs\Python\Python313\python.exe' divert_nat.py *> 'C:\Users\chris\uo-harness\divert.log'
