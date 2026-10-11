# ============================================================================
#  Liga o Portal do Colaborador em MODO SERVIDOR (produção) neste PC.
#  Normalmente NÃO é preciso rodar à mão: o instalar_servidor.ps1 agenda este
#  script para ligar sozinho junto com o Windows.
#
#  - O portal fica em http://127.0.0.1:8020 (só este PC acessa direto).
#  - A internet chega nele pelo túnel da Cloudflare (link fixo do domínio).
#  - O domínio vem da linha DOMINIO_PUBLICO do arquivo .env
# ============================================================================
param([int]$Porta = 8020)
# "Continue": no PowerShell 5.1, as mensagens normais do servidor (stderr) não podem derrubar o script.
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)  # roda a partir da pasta do projeto
New-Item -ItemType Directory -Force logs | Out-Null
$log = "logs\portal.log"

# Modo produção: vale só para este processo (o .env continua em modo de desenvolvimento para o runserver).
$env:DEBUG = "False"
$env:ALLOWED_HOSTS = "localhost,127.0.0.1"
$env:CONFIAR_CABECALHO_PROXY = "True"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

function Registrar($texto) {
    [IO.File]::AppendAllText("$PWD\$log", "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  $texto`r`n")
}
# Roda pelo cmd para gravar a saída no log como texto simples (sem conversões do PowerShell).
function Rodar($comando) { cmd /c "$comando >> $log 2>&1"; return $LASTEXITCODE }

Registrar "Iniciando o portal na porta $Porta..."
Rodar ".venv\Scripts\python.exe manage.py migrate --noinput" | Out-Null
if ((Rodar ".venv\Scripts\python.exe manage.py verificar_publicacao") -ne 0) {
    Registrar "PARADO: a verificação de segurança falhou (veja acima)."; exit 1
}
Rodar ".venv\Scripts\python.exe manage.py collectstatic --noinput" | Out-Null
Rodar ".venv\Scripts\python.exe manage.py clearsessions" | Out-Null  # apaga sessões vencidas

Registrar "Portal no ar em http://127.0.0.1:$Porta"
$codigo = Rodar (".venv\Scripts\waitress-serve.exe --listen=127.0.0.1:$Porta --threads=16 --channel-timeout=300 " +
                 "--trusted-proxy=127.0.0.1 `"--trusted-proxy-headers=x-forwarded-for x-forwarded-proto`" portal.wsgi:application")
Registrar "O portal parou (código $codigo)."
exit 1  # faz o Agendador de Tarefas religar sozinho
