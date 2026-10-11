"""Telas da verificação em duas etapas do painel do RH (ativar, gerenciar e digitar o código no login).
Entram pelo admin_view do painel, que já exige login e desliga o cache do navegador."""
from django.contrib import messages
from django.contrib.auth import logout
from django.core.cache import cache
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from auditoria.models import LogAuditoria
from auditoria.servicos import registrar

from . import duas_etapas
from .middleware import CHAVE_VERIFICADO, verificado
from .servicos import bloquear

CHAVE_SEGREDO_NOVO = "duas_etapas_segredo_novo"
LIMITE_ERROS = 5
JANELA_ERROS = 15 * 60


def _destino(request):
    proximo = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(proximo, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return proximo
    return reverse("admin:index")


def _conferir_codigo(usuario, digitado):
    """Código do app (uso único) ou código reserva. Salva o que mudou. True se aceitou."""
    passo = duas_etapas.conferir(usuario.duas_etapas_segredo, digitado, usuario.duas_etapas_ultimo_passo)
    if passo is not None:
        usuario.duas_etapas_ultimo_passo = passo
        usuario.save(update_fields=["duas_etapas_ultimo_passo"])
        return True
    restantes = duas_etapas.usar_codigo_reserva(usuario.duas_etapas_reserva, digitado)
    if restantes is not None:
        usuario.duas_etapas_reserva = restantes
        usuario.save(update_fields=["duas_etapas_reserva"])
        return True
    return False


def _contexto(site, request, **extra):
    return {**site.each_context(request), **extra}


def verificar(site, request):
    """Segunda etapa do login: o código de 6 números do celular (ou um código reserva)."""
    usuario = request.user
    if not usuario.duas_etapas_ativa or verificado(request):
        return redirect(_destino(request))
    chave_erros = f"duas_etapas_erros:{usuario.pk}"
    erro = ""
    if request.method == "POST":
        if _conferir_codigo(usuario, request.POST.get("codigo", "")):
            cache.delete(chave_erros)
            request.session.cycle_key()
            request.session[CHAVE_VERIFICADO] = usuario.pk
            if len(usuario.duas_etapas_reserva) <= 2:
                messages.warning(request, f"Restam só {len(usuario.duas_etapas_reserva)} códigos reserva. Gere novos "
                                          "em Verificação em duas etapas.")
            return redirect(_destino(request))
        erros = cache.get(chave_erros, 0) + 1
        cache.set(chave_erros, erros, JANELA_ERROS)
        registrar(LogAuditoria.Acao.DUAS_ETAPAS_FALHA, request=request, usuario=usuario,
                  descricao=f"Tentativa {erros} de {LIMITE_ERROS}")
        if erros >= LIMITE_ERROS:
            cache.delete(chave_erros)
            bloquear(usuario)  # trava o login por 15 minutos, como na senha errada
            registrar(LogAuditoria.Acao.CONTA_BLOQUEADA, request=request, usuario=usuario,
                      descricao="Muitos códigos errados na verificação em duas etapas: travado por 15 minutos")
            logout(request)
            messages.error(request, "Muitos códigos errados. O login foi travado por 15 minutos.")
            return redirect("admin:login")
        erro = "Código errado ou vencido. Confira o app e tente de novo."
    return TemplateResponse(request, "admin/duas_etapas/verificar.html", _contexto(
        site, request, title="Verificação em duas etapas", erro=erro, next=_destino(request), menu_lateral=None))


def configurar(site, request):
    """Ativar (QR code + confirmar um código), gerar novos códigos reserva ou desativar."""
    usuario = request.user
    codigos_novos = None
    if request.method == "POST":
        acao = request.POST.get("acao")
        digitado = request.POST.get("codigo", "")
        if acao == "ativar":
            segredo = request.session.get(CHAVE_SEGREDO_NOVO, "")
            passo = duas_etapas.conferir(segredo, digitado)
            if passo is None:
                messages.error(request, "Código errado. Confira se o relógio do celular está certo e digite o "
                                        "código que aparece agora no app.")
            else:
                codigos_novos, guardados = duas_etapas.novos_codigos_reserva()
                usuario.duas_etapas_segredo, usuario.duas_etapas_ultimo_passo = segredo, passo
                usuario.duas_etapas_reserva, usuario.duas_etapas_ativa = guardados, True
                usuario.save(update_fields=["duas_etapas_segredo", "duas_etapas_ultimo_passo", "duas_etapas_reserva",
                                            "duas_etapas_ativa"])
                request.session.pop(CHAVE_SEGREDO_NOVO, None)
                request.session[CHAVE_VERIFICADO] = usuario.pk
                registrar(LogAuditoria.Acao.DUAS_ETAPAS_ATIVADA, request=request, usuario=usuario)
        elif acao in ("desativar", "novos_codigos") and usuario.duas_etapas_ativa:
            if not _conferir_codigo(usuario, digitado):
                messages.error(request, "Código errado: para mudar a verificação, digite o código atual do app.")
            elif acao == "desativar":
                usuario.duas_etapas_ativa, usuario.duas_etapas_segredo, usuario.duas_etapas_reserva = False, "", []
                usuario.save(update_fields=["duas_etapas_ativa", "duas_etapas_segredo", "duas_etapas_reserva"])
                registrar(LogAuditoria.Acao.DUAS_ETAPAS_DESATIVADA, request=request, usuario=usuario)
                messages.warning(request, "Verificação em duas etapas desativada. O login volta a pedir só a senha.")
                return redirect("admin:duas_etapas_configurar")
            else:
                codigos_novos, usuario.duas_etapas_reserva = duas_etapas.novos_codigos_reserva()
                usuario.save(update_fields=["duas_etapas_reserva"])
        if codigos_novos is None:
            return redirect("admin:duas_etapas_configurar")

    contexto = {"title": "Verificação em duas etapas", "codigos_novos": codigos_novos,
                "reserva_restantes": len(usuario.duas_etapas_reserva)}
    if not usuario.duas_etapas_ativa:
        segredo = request.session.get(CHAVE_SEGREDO_NOVO) or duas_etapas.novo_segredo()
        request.session[CHAVE_SEGREDO_NOVO] = segredo
        contexto.update(qr=duas_etapas.qr_code_svg(duas_etapas.endereco_para_o_app(segredo, usuario.get_username())),
                        segredo=duas_etapas.segredo_formatado(segredo))
    return TemplateResponse(request, "admin/duas_etapas/configurar.html", _contexto(site, request, **contexto))
