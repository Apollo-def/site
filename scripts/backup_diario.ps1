# ============================================================================
#  Backup diário do portal (banco + PDFs) em dados_rh\backups.
#  Guarda os 7 mais recentes. Agendado pelo instalar_servidor.ps1 para 02:00.
#  A cópia para fora do PC é feita pelo Google Drive para computador (conta da empresa),
#  que sincroniza a pasta dados_rh\backups (Preferências > Meu laptop > Adicionar pasta).
# ============================================================================
$ErrorActionPreference = "Continue"
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force logs | Out-Null
$env:PYTHONUTF8 = "1"

cmd /c ".venv\Scripts\python.exe manage.py exportar_backup >> logs\backup.log 2>&1"
cmd /c ".venv\Scripts\python.exe manage.py clearsessions >> logs\backup.log 2>&1"  # apaga sessões vencidas
cmd /c ".venv\Scripts\python.exe manage.py limpar_whatsapp >> logs\backup.log 2>&1"  # LGPD: conversas antigas
Get-ChildItem dados_rh\backups\portal_*.zip | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 7 | Remove-Item -Force
