# ============================================================================
#  Liga o gateway do WhatsApp (conexão por QR code).
#      powershell -ExecutionPolicy Bypass -File .\scripts\gateway_whatsapp.ps1
#  Na primeira vez: instala as dependências (npm), cria a senha WHATSAPP_GATEWAY_TOKEN no .env e
#  liga WHATSAPP_CANAL=gateway (reinicie o portal depois, para ele ler o .env novo).
#  Depois: painel do RH > WhatsApp > Conexão mostra o QR code. Registros em logs\gateway.log
#  ATENÇÃO: conexão não oficial — use um chip secundário, não o número oficial da empresa.
# ============================================================================
$ErrorActionPreference = "Stop"
$projeto = Split-Path $PSScriptRoot -Parent
Set-Location $projeto
New-Item -ItemType Directory -Force logs | Out-Null

$node = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $node -and (Test-Path "$env:ProgramFiles\nodejs\node.exe")) { $node = "$env:ProgramFiles\nodejs\node.exe" }
if (-not $node) { throw "Node.js não encontrado. Instale a versão LTS em https://nodejs.org e rode de novo." }
$pastaNode = Split-Path $node -Parent
$env:Path = "$pastaNode;$env:Path"

if (-not (Test-Path "gateway-whatsapp\node_modules")) {
    Write-Host "Instalando as dependências do gateway (só na primeira vez)..." -ForegroundColor Cyan
    Push-Location gateway-whatsapp
    & "$pastaNode\npm.cmd" ci --omit=dev --no-audit --no-fund
    $codigo = $LASTEXITCODE
    Pop-Location
    if ($codigo -ne 0) { throw "Falha ao instalar as dependências (npm ci)." }
}

# Senha entre o portal e o gateway + canal "gateway" no .env (só se ainda não existirem).
$linhas = @(if (Test-Path .env) { Get-Content .env -Encoding utf8 })
$mudou = $false
if (-not ($linhas | Where-Object { $_ -match "^WHATSAPP_GATEWAY_TOKEN=.{20,}" })) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $linhas = @($linhas | Where-Object { $_ -notmatch "^WHATSAPP_GATEWAY_TOKEN=" })
    $linhas += "WHATSAPP_GATEWAY_TOKEN=" + (($bytes | ForEach-Object { $_.ToString("x2") }) -join "")
    $mudou = $true
}
if (-not ($linhas | Where-Object { $_ -match "^WHATSAPP_CANAL=" })) {
    $linhas += "WHATSAPP_CANAL=gateway"
    $mudou = $true
}
if ($mudou) {
    [IO.File]::WriteAllLines("$projeto\.env", [string[]]$linhas, (New-Object Text.UTF8Encoding $false))  # UTF-8 sem BOM
    Write-Host "Configuração do gateway gravada no .env. REINICIE o portal para ele usar a nova senha." -ForegroundColor Yellow
}

Write-Host "Gateway do WhatsApp ligado. Registros em logs\gateway.log" -ForegroundColor Green
Set-Location gateway-whatsapp
cmd /c "`"$node`" gateway.mjs >> ..\logs\gateway.log 2>&1"
