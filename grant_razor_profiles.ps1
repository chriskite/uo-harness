# Lets the viz Razor page (harness/razor.py) save hotkeys into the Razor profiles:
# grants the current user Modify on the install's Razor Profiles folder ONLY
# (ClassicUO\Data\Plugins\Assistant\Profiles; BUILTIN\Users has just RX there by default).
# User-approved exception to AGENTS.md Constraint 1 (2026-10-11); nothing else in the
# install dir changes. Undo with: -Remove
# Run from an ADMINISTRATOR PowerShell (it does not self-elevate). Execution policy is unset
# (= Restricted) on these machines, so invoke it as:
#   powershell -ExecutionPolicy Bypass -File .\grant_razor_profiles.ps1
param([switch]$Remove)

# The enum, not the string "Administrator": IsInRole(string) looks up a group by that name, and
# the group is BUILTIN\Administrators, so the string form is false even when elevated.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Error "Run this from an elevated (Administrator) PowerShell."
    exit 1
}

$dir = Join-Path ${env:ProgramFiles(x86)} "Ultima Online Outlands\ClassicUO\Data\Plugins\Assistant\Profiles"
if (-not (Test-Path -LiteralPath $dir -PathType Container)) {
    Write-Error "no Razor Profiles folder at $dir"
    exit 1
}
$user = "$env:USERDOMAIN\$env:USERNAME"
if ($Remove) {
    icacls $dir /remove:g $user | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Error "icacls failed ($LASTEXITCODE)"; exit 1 }
    Write-Host "removed $user from $dir"
} else {
    icacls $dir /grant "${user}:(OI)(CI)M" | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Error "icacls failed ($LASTEXITCODE)"; exit 1 }
    Write-Host "granted Modify on $dir to $user"
}
