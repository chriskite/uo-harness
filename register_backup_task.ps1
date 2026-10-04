# Registers (or updates) the hourly "uo-harness backup" scheduled task: harness\backup.py -> NAS
# (docs/NOTES.md "Backups"). Runs as the current user, non-elevated, only while logged on
# (no stored password; the share is reached over the logon session's SMB connection).
# pythonw.exe (next to the python.exe on PATH, or $env:UO_PY) so no console window ever pops over
# the game.
$python = $env:UO_PY
if (-not $python) {
    $python = (Get-Command python.exe -CommandType Application -ErrorAction SilentlyContinue |
        Where-Object { $_.Source -notlike '*\WindowsApps\*' } | Select-Object -First 1).Source
}
if (-not $python) { throw 'python.exe (3.13) not found on PATH; set UO_PY' }
$py = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
if (-not (Test-Path $py)) { throw "pythonw.exe not found next to $python" }
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$root\harness\backup.py`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "uo-harness backup" -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force | Out-Null
Get-ScheduledTask -TaskName "uo-harness backup" | Get-ScheduledTaskInfo | Format-List TaskName, NextRunTime, LastRunTime, LastTaskResult
