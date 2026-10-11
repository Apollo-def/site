import ipaddress
import logging

from django.conf import settings

from .models import LogAuditoria

logger = logging.getLogger(__name__)


def obter_ip(request):
    ip = None
    if getattr(settings, "CONFIAR_CABECALHO_PROXY", False):
        encaminhado = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if encaminhado:
            # Vale o ÚLTIMO endereço: é o que o nosso proxy (Cloudflare, Coolify...) acrescenta.
            # Os anteriores vêm do próprio visitante e podem ser inventados para driblar o bloqueio por IP.
            ip = encaminhado.split(",")[-1].strip()
    ip = ip or request.META.get("REMOTE_ADDR")
    try:
        return str(ipaddress.ip_address(ip)) if ip else None
    except ValueError:
        return None


def registrar(acao, request=None, usuario=None, descricao="", objeto=None, identificador=""):
    """
    Grava uma linha no log de auditoria. Exemplo:
        registrar(LogAuditoria.Acao.BAIXOU_DOCUMENTO, request=request, objeto=contracheque)
    Se o usuário não for informado, usa o usuário logado da requisição.
    """
    if usuario is None and request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        usuario = request.user
    try:
        return LogAuditoria.objects.create(
            acao=acao,
            usuario=usuario,
            identificador=(identificador or (usuario.get_username() if usuario else ""))[:150],
            descricao=descricao,
            objeto_tipo=str(objeto._meta.verbose_name)[:60] if objeto is not None else "",
            objeto_id=str(objeto.pk) if objeto is not None else "",
            ip=obter_ip(request) if request is not None else None,
            user_agent=(request.META.get("HTTP_USER_AGENT", "") if request is not None else "")[:300],
        )
    except Exception:  # a auditoria nunca deve derrubar a tela do usuário
        logger.exception("Falha ao gravar log de auditoria (%s)", acao)
        return None
