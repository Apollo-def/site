# ============================================================================
#  Compartilha o Portal do Colaborador pela internet com o ngrok (link temporário)
#
#  Como usar (no terminal do VS Code, na pasta do projeto):
#      powershell -ExecutionPolicy Bypass -File .\scripts\compartilhar_ngrok.ps1
#
#  - O link https://....ngrok-free.app aparece na tela do ngrok ("Forwarding").
#  - Funciona só enquanto este terminal e o computador estiverem ligados.
#  - Para encerrar: Ctrl+C.
#  - Enquanto estiver compartilhando, use o LINK DO NGROK também no seu computador
#    (o modo público exige HTTPS; o endereço 127.0.0.1 não faz login neste modo).
# ============================================================================
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)  # roda a partir da pasta do projeto
$python = ".\.venv\Scripts\python.exe"
$porta = 8020

# Painel do RH num endereço secreto (e não /admin/, o primeiro que os robôs tentam). Criado uma vez no .env.
$linhas = @(Get-Content .env -Encoding utf8)
$painel = ($linhas | Where-Object { $_ -match "^ADMIN_URL=" } | Select-Object -First 1) -replace "^ADMIN_URL=", ""
if (-not $painel) {
    $painel = "painel-rh-" + (-join ((48..57) + (97..102) | Get-Random -Count 6 | ForEach-Object { [char]$_ })) + "/"
    $linhas += "ADMIN_URL=$painel"
    [IO.File]::WriteAllLines("$PWD\.env", [string[]]$linhas, (New-Object Text.UTF8Encoding $false))  # UTF-8 sem BOM
}
Write-Host "Painel do RH: <link do ngrok>/$painel  (anote! também vale em http://127.0.0.1:8010/$painel)" -ForegroundColor Green

# Modo público: valem só para este terminal (o .env continua em modo de desenvolvimento).
$env:DEBUG = "False"
$env:ALLOWED_HOSTS = "localhost,127.0.0.1,.ngrok-free.app,.ngrok-free.dev,.ngrok.app,.ngrok.dev,.ngrok.io"
$env:CSRF_TRUSTED_ORIGINS = "https://*.ngrok-free.app,https://*.ngrok-free.dev,https://*.ngrok.app,https://*.ngrok.dev,https://*.ngrok.io"
$env:CONFIAR_CABECALHO_PROXY = "True"

Write-Host "1/3 Conferindo a segurança..." -ForegroundColor Cyan
& $python manage.py verificar_publicacao
if ($LASTEXITCODE -ne 0) { Write-Host "Corrija os itens acima e rode de novo." -ForegroundColor Yellow; exit 1 }

Write-Host "2/3 Preparando o visual (CSS/JS)..." -ForegroundColor Cyan
& $python manage.py collectstatic --noinput | Out-Null

Write-Host "3/3 Ligando o portal e o ngrok..." -ForegroundColor Cyan
$servidor = Start-Process -FilePath ".\.venv\Scripts\waitress-serve.exe" -NoNewWindow -PassThru -ArgumentList @(
    "--listen=127.0.0.1:$porta", "--threads=8",
    "--trusted-proxy=127.0.0.1", "`"--trusted-proxy-headers=x-forwarded-for x-forwarded-proto`"",
    "portal.wsgi:application"
)
$ngrok = Join-Path $env:LOCALAPPDATA "ngrok\ngrok.exe"
if (-not (Test-Path $ngrok)) { $ngrok = "ngrok" }
# Painel interno do ngrok na porta 4041, para não brigar com outro ngrok já aberto (que usa a 4040).
$configPadrao = Join-Path $env:LOCALAPPDATA "ngrok\ngrok.yml"
$configExtra = Join-Path $env:TEMP "ngrok_portal_colaborador.yml"
Set-Content -Path $configExtra -Encoding ascii -Value "version: `"2`"`nweb_addr: 127.0.0.1:4041"
try {
    & $ngrok http $porta --config "$configPadrao,$configExtra"
}
finally {
    Stop-Process -Id $servidor.Id -ErrorAction SilentlyContinue
    Write-Host "Compartilhamento encerrado. O link deixou de funcionar." -ForegroundColor Yellow
}
