import os

from django.conf import settings
from django.contrib.admin.views.decorators import staff_member_required
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.shortcuts import render
from django.utils.crypto import constant_time_compare
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from auditoria.models import LogAuditoria
from auditoria.servicos import obter_ip, registrar
from contas.models import ConfiguracaoPortal

from .backup import ErroBackup, banco_vazio, importar


@never_cache
def inicio(request):
    """Página pública: os funcionários são atendidos pelo WhatsApp (não há login de funcionário)."""
    return render(request, "publico/inicio.html", {"config": ConfiguracaoPortal.atual()})


def formulario_expirado(request, reason=""):
    """No lugar da página técnica do Django: o formulário ficou velho (outra aba, login novo, página aberta há muito)."""
    voltar = request.META.get("HTTP_REFERER", "")
    if not url_has_allowed_host_and_scheme(voltar, allowed_hosts={request.get_host()}):
        voltar = ""  # só oferece "voltar" para uma página do próprio portal
    return render(request, "formulario_expirado.html", {"voltar": voltar}, status=403)


MAXIMO_TENTATIVAS_RESTAURAR = 5


@never_cache
@csrf_protect
def restaurar_backup(request):
    """
    Traz os dados do computador para o servidor (uma única vez). Exige a senha da variável
    RESTAURAR_BACKUP_SENHA e só funciona com o banco vazio: depois de restaurar, a tela se desliga.
    """
    if not settings.RESTAURAR_BACKUP_SENHA or not banco_vazio():
        raise Http404
    chave_ip = f"restaurar_backup_falhas:{obter_ip(request)}"
    erro = None
    if request.method == "POST":
        if cache.get(chave_ip, 0) >= MAXIMO_TENTATIVAS_RESTAURAR:
            erro = "Muitas tentativas erradas. Aguarde 15 minutos."
        elif not constant_time_compare(request.POST.get("senha", ""), settings.RESTAURAR_BACKUP_SENHA):
            cache.set(chave_ip, cache.get(chave_ip, 0) + 1, 15 * 60)
            erro = "Senha incorreta."
        elif "arquivo" not in request.FILES:
            erro = "Escolha o arquivo .zip do backup."
        else:
            arquivo = request.FILES["arquivo"]
            origem = arquivo.temporary_file_path() if hasattr(arquivo, "temporary_file_path") else arquivo
            try:
                total = importar(origem)
            except ErroBackup as falha:
                erro = str(falha)
            else:
                registrar(LogAuditoria.Acao.IMPORTACAO_FUNCIONARIOS, request=request,
                          descricao=f"Restauração de backup '{arquivo.name}' ({total} arquivos)")
                return render(request, "restaurar_backup.html", {"concluido": True, "total": total})
    return render(request, "restaurar_backup.html", {"erro": erro})


@staff_member_required
def arquivo_privado_admin(request, caminho):
    """Abre um arquivo privado para o RH conferir. Só administradores entram aqui."""
    if not default_storage.exists(caminho):
        raise Http404("Arquivo não encontrado.")
    registrar(LogAuditoria.Acao.ADMIN_ABRIU_ARQUIVO, request=request, descricao=caminho)
    return FileResponse(default_storage.open(caminho, "rb"), filename=os.path.basename(caminho))
