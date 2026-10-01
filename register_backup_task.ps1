# Registers (or updates) the hourly "uo-harness backup" scheduled task: harness\backup.py -> NAS
# (docs/NOTES.md "Backups"). Runs as the current user, non-elevated, only while logged on
# (no stored password; the share is reached over the logon session's SMB connection).
# pythonw.exe so no console window ever pops over the game.
$py = "C:\Users\chris\AppData\Local\Programs\Python\Python313\pythonw.exe"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$root\harness\backup.py`"" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).Date -RepetitionInterval (New-TimeSpan -Hours 1)
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName "uo-harness backup" -Action $action -Trigger $trigger -Settings $settings `
    -Principal $principal -Force | Out-Null
Get-ScheduledTask -TaskName "uo-harness backup" | Get-ScheduledTaskInfo | Format-List TaskName, NextRunTime, LastRunTime, LastTaskResult
