# Lets other devices on the home LAN reach the viz server (harness/viz_server.py --host 0.0.0.0).
# Inbound TCP <port>, Private profile only, remote addresses limited to the local subnet.
# Run from an ADMINISTRATOR PowerShell (it does not self-elevate; an earlier self-elevating
# version relaunched itself in a loop). Idempotent. Remove the rule with: -Remove
# Execution policy is unset (= Restricted) on these machines, so invoke it as:
#   powershell -ExecutionPolicy Bypass -File .\allow_viz_lan.ps1
param([int]$Port = 8080, [switch]$Remove)

$name = "uo-harness viz $Port"
# The enum, not the string "Administrator": IsInRole(string) looks up a group by that name, and
# the group is BUILTIN\Administrators, so the string form is false even when elevated.
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
    Write-Error "Run this from an elevated (Administrator) PowerShell."
    exit 1
}

Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
if ($Remove) {
    Write-Host "removed firewall rule '$name'"
} else {
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow -Protocol TCP `
        -LocalPort $Port -Profile Private -RemoteAddress LocalSubnet | Out-Null
    Write-Host "added firewall rule '$name'"
}
