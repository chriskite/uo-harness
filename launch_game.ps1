# Launches the Outlands client through the harness proxy (127.0.0.1:2593).
# Elevation is REQUIRED (client needs write access to the install dir; see docs/NOTES.md).
Start-Process -FilePath 'C:\Program Files (x86)\Ultima Online Outlands\ClassicUO\ClassicUO.exe' `
    -ArgumentList '-ip','127.0.0.1','-port','2593' `
    -WorkingDirectory 'C:\Program Files (x86)\Ultima Online Outlands\ClassicUO' `
    -Verb RunAs
Write-Output "launched (UAC prompt must be accepted)"
