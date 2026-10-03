# Lets other devices on the home LAN reach the viz server (harness/viz_server.py --host 0.0.0.0).
# Inbound TCP <port>, Private profile only, remote addresses limited to the local subnet.
# Run from an ADMINISTRATOR PowerShell (it does not self-elevate; an earlier self-elevating
# version relaunched itself in a loop). Idempotent. Remove the rule with: -Remove
param([int]$Port = 8080, [switch]$Remove)

$name = "uo-harness viz $Port"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole("Administrator")
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
