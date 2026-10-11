# ============================================================================
#  Troca a senha de um login do painel do RH (mostra * enquanto digita e aceita colar).
#  Na pasta do projeto:
#      powershell -ExecutionPolicy Bypass -File .\scripts\trocar_senha_rh.ps1
#      powershell -ExecutionPolicy Bypass -File .\scripts\trocar_senha_rh.ps1 -Usuario outro_login
#  Também zera as tentativas erradas e destrava o login.
# ============================================================================
param([string]$Usuario = "grupo_global")
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

function LerSenha($texto) {
    $segura = Read-Host $texto -AsSecureString
    $ponteiro = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($segura)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ponteiro) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ponteiro) }
}

Write-Host "Trocando a senha do login '$Usuario'." -ForegroundColor Cyan
$senha = LerSenha "Nova senha"
if (-not $senha) { Write-Host "A senha não pode ficar vazia." -ForegroundColor Yellow; exit 1 }
if ($senha -ne (LerSenha "Repita a nova senha")) { Write-Host "As duas senhas não são iguais. Nada foi alterado." -ForegroundColor Yellow; exit 1 }

# A senha vai para o Python por variável de ambiente (só deste processo) e nunca aparece na tela.
$env:NOVA_SENHA_RH = $senha
$env:PYTHONUTF8 = "1"
try {
    & .\.venv\Scripts\python.exe manage.py trocar_senha_rh $Usuario
} finally {
    Remove-Item Env:NOVA_SENHA_RH -ErrorAction SilentlyContinue
}
