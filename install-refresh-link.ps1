# One-time setup on the laptop: make jobradar://refresh links run the laptop half of a refresh.
#   ./install-refresh-link.ps1              register (or re-register after moving this folder)
#   ./install-refresh-link.ps1 -Uninstall   remove
#
# The website's Refresh button (on the server) opens jobradar://refresh. Windows looks the
# scheme up in the registry and runs the command below: `./refresh --source workday` in a
# PowerShell window. The command is fixed and ignores the rest of the URL, so no link can
# pass arguments into it. Per-user registry key (HKCU): no admin rights needed.
param([switch]$Uninstall)

$key = "HKCU:\Software\Classes\jobradar"

if ($Uninstall) {
    Remove-Item -Path $key -Recurse -ErrorAction SilentlyContinue
    Write-Host "Removed the jobradar:// link."
    return
}

$script = Join-Path $PSScriptRoot "refresh.ps1"
$command = "powershell.exe -NoProfile -Command `"& '$script' --source workday; Write-Host; Read-Host 'Done. Press Enter to close'`""

New-Item -Path "$key\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path $key -Name "(Default)" -Value "URL:JobRadar laptop refresh"
Set-ItemProperty -Path $key -Name "URL Protocol" -Value ""
Set-ItemProperty -Path "$key\shell\open\command" -Name "(Default)" -Value $command

Write-Host "Registered jobradar://refresh -> $script --source workday"
Write-Host "The first time you click Refresh on the site, the browser asks to open PowerShell: allow it."
