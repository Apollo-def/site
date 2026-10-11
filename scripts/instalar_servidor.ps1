# ============================================================================
#  Transforma ESTE PC no servidor do Portal do Colaborador (link fixo).
#  Rode UMA vez, no PowerShell aberto como ADMINISTRADOR, na pasta do projeto:
#
#      powershell -ExecutionPolicy Bypass -File .\scripts\instalar_servidor.ps1 `
#          -Dominio portal.SEU-DOMINIO.com.br -TokenCloudflare "COLE_O_TOKEN_AQUI"
#
#  O token vem do painel da Cloudflare (Zero Trust > Networks > Tunnels > seu túnel).
#  O que ele faz:
#    1. Grava o domínio no .env (DOMINIO_PUBLICO).
#    2. Agenda o portal para ligar sozinho com o Windows (e religar se cair).
#    3. Instala o túnel da Cloudflare como serviço do Windows (link fixo).
#    4. Agenda o backup diário às 02:00.
#    5. Impede o PC de entrar em modo de espera/hibernar na tomada.
#  Para desfazer tudo: scripts\desinstalar_servidor.ps1
# ============================================================================
param(
    [Parameter(Mandatory = $true)][string]$Dominio,
    [Parameter(Mandatory = $true)][string]$TokenCloudflare
)
$ErrorActionPreference = "Stop"
$projeto = Split-Path $PSScriptRoot -Parent
Set-Location $projeto

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw "Abra o PowerShell como ADMINISTRADOR (botão direito > Executar como administrador) e rode de novo." }
$Dominio = ($Dominio -replace "^https?://", "").TrimEnd("/")
if (Get-NetTCPConnection -LocalPort 8020 -State Listen -ErrorAction SilentlyContinue) {
    throw "A porta 8020 já está em uso (provavelmente o compartilhar_ngrok/cloudflare). Feche esse terminal e rode de novo."
}

Write-Host "1/5 Gravando o domínio no .env..." -ForegroundColor Cyan
$linhas = @(Get-Content .env -Encoding utf8 | Where-Object { $_ -notmatch "^DOMINIO_PUBLICO=" })
$linhas += "DOMINIO_PUBLICO=$Dominio"
# Painel do RH num endereço secreto (e não /admin/, que todo mundo tenta). Sorteado aqui, nunca vai para o GitHub.
$painel = ($linhas | Where-Object { $_ -match "^ADMIN_URL=" } | Select-Object -First 1) -replace "^ADMIN_URL=", ""
if (-not $painel) {
    $painel = "painel-rh-" + (-join ((48..57) + (97..102) | Get-Random -Count 6 | ForEach-Object { [char]$_ })) + "/"
    $linhas += "ADMIN_URL=$painel"
}
[IO.File]::WriteAllLines("$projeto\.env", [string[]]$linhas, (New-Object Text.UTF8Encoding $false))  # UTF-8 sem BOM

Write-Host "2/5 Conferindo a segurança antes de publicar..." -ForegroundColor Cyan
$env:DEBUG = "False"
& .\.venv\Scripts\python.exe manage.py verificar_publicacao
if ($LASTEXITCODE -ne 0) { throw "Corrija os itens acima e rode de novo." }
Remove-Item Env:DEBUG

Write-Host "3/5 Agendando o portal e o backup diário..." -ForegroundColor Cyan
$sistema = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$config = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
$portal = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $projeto `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$projeto\scripts\servidor.ps1`""
Register-ScheduledTask -TaskName "PortalColaborador" -Force -Principal $sistema -Settings $config `
    -Trigger (New-ScheduledTaskTrigger -AtStartup) -Action $portal `
    -Description "Portal do Colaborador (liga com o Windows)" | Out-Null
$backup = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $projeto `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$projeto\scripts\backup_diario.ps1`""
$configBackup = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "PortalColaboradorBackup" -Force -Principal $sistema -Settings $configBackup `
    -Trigger (New-ScheduledTaskTrigger -Daily -At "02:00") -Action $backup `
    -Description "Backup diário do Portal do Colaborador" | Out-Null
Start-ScheduledTask -TaskName "PortalColaborador"
# Gateway do WhatsApp (QR code): só se o .env usar WHATSAPP_CANAL=gateway.
if (Get-Content .env -Encoding utf8 | Where-Object { $_ -match "^WHATSAPP_CANAL=gateway" }) {
    $gateway = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $projeto `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$projeto\scripts\gateway_whatsapp.ps1`""
    Register-ScheduledTask -TaskName "PortalWhatsApp" -Force -Principal $sistema -Settings $config `
        -Trigger (New-ScheduledTaskTrigger -AtStartup) -Action $gateway `
        -Description "Gateway do WhatsApp do Portal do Colaborador (QR code)" | Out-Null
    Start-ScheduledTask -TaskName "PortalWhatsApp"
}

Write-Host "4/5 Instalando o túnel da Cloudflare (link fixo)..." -ForegroundColor Cyan
$pasta = "$env:ProgramFiles\cloudflared"
$cloudflared = "$pasta\cloudflared.exe"
if (-not (Test-Path $cloudflared)) {
    New-Item -ItemType Directory -Force $pasta | Out-Null
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -OutFile $cloudflared `
        -Uri "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
}
if (Get-Service cloudflared -ErrorAction SilentlyContinue) { & $cloudflared service uninstall | Out-Null }
& $cloudflared service install $TokenCloudflare

Write-Host "5/5 Desligando o modo de espera na tomada..." -ForegroundColor Cyan
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

Write-Host "Aguardando o portal ligar..." -ForegroundColor Cyan
$ok = $false
foreach ($i in 1..60) {
    try { if ((Invoke-WebRequest "http://127.0.0.1:8020/" -UseBasicParsing -TimeoutSec 3 -MaximumRedirection 0 -ErrorAction Stop).StatusCode) { $ok = $true; break } }
    catch { if ($_.Exception.Response) { $ok = $true; break } }
    Start-Sleep -Seconds 2
}
if ($ok) {
    Write-Host "`nPRONTO! Portal no ar em: https://$Dominio" -ForegroundColor Green
    Write-Host "Painel do RH:            https://$Dominio/$painel  (anote! também vale no 127.0.0.1)" -ForegroundColor Green
    Write-Host "Ele liga sozinho quando o PC ligar. Registros em logs\portal.log" -ForegroundColor Green
} else {
    Write-Host "`nO portal não respondeu. Veja o arquivo logs\portal.log" -ForegroundColor Yellow
}
