# ============================================================================
#  Desfaz o instalar_servidor.ps1: tira o portal, o backup e o túnel do início do Windows.
#  Os dados NÃO são apagados. Rode como ADMINISTRADOR:
#      powershell -ExecutionPolicy Bypass -File .\scripts\desinstalar_servidor.ps1
# ============================================================================
$ErrorActionPreference = "Continue"
foreach ($tarefa in "PortalColaborador", "PortalColaboradorBackup", "PortalWhatsApp") {
    Stop-ScheduledTask -TaskName $tarefa -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $tarefa -Confirm:$false -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match "waitress-serve.*8020|gateway\.mjs" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
$cloudflared = "$env:ProgramFiles\cloudflared\cloudflared.exe"
if (Test-Path $cloudflared) { & $cloudflared service uninstall }
Write-Host "Servidor desinstalado. Os dados continuam no PC." -ForegroundColor Green
