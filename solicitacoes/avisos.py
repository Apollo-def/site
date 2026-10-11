"""
Aviso por e-mail para o RH quando chega um pedido novo.

Configuração no .env (sem ela, nada é enviado e o portal funciona normalmente):
    EMAIL_HOST=smtp.gmail.com
    EMAIL_HOST_USER=portal@empresa.com.br
    EMAIL_HOST_PASSWORD=senha-de-app        (Gmail: "senha de app", não a senha normal)
    EMAILS_AVISO_RH=rh1@empresa.com.br,rh2@empresa.com.br
Teste: python manage.py testar_email
O CPF não vai no e-mail: só nome, matrícula, contrato e o link para o painel.
"""
import logging
import threading

from django.conf import settings
from django.core.mail import send_mail
from django.db import connection, transaction
from django.urls import reverse

logger = logging.getLogger(__name__)


def email_configurado():
    return bool(settings.EMAIL_HOST and settings.EMAILS_AVISO_RH)


def montar_aviso(solicitacao):
    f = solicitacao.funcionario
    link = settings.PORTAL_URL.rstrip("/") + reverse("admin:solicitacoes_solicitacao_change", args=[solicitacao.pk])
    assunto = f"Novo pedido no portal: {solicitacao.protocolo} — {solicitacao.tipo}"
    corpo = (
        f"Chegou um novo pedido no Portal do Colaborador.\n\n"
        f"Protocolo: {solicitacao.protocolo}\n"
        f"Pedido: {solicitacao.tipo}\n"
        f"Funcionário: {f.nome_completo} (matrícula {f.matricula}, {f.contrato})\n"
        + (f"Observação: {solicitacao.observacao_funcionario}\n" if solicitacao.observacao_funcionario else "")
        + f"\nAbrir no painel do RH: {link}\n"
    )
    return assunto, corpo


def _enviar(solicitacao_id):
    from .models import Solicitacao  # import tardio: roda também em thread separada
    try:
        assunto, corpo = montar_aviso(Solicitacao.objects.select_related("funcionario__contrato", "tipo")
                                      .get(pk=solicitacao_id))
        send_mail(assunto, corpo, None, settings.EMAILS_AVISO_RH)
    except Exception:  # o aviso nunca pode atrapalhar o pedido do funcionário
        logger.exception("Falha ao enviar o aviso de nova solicitação %s", solicitacao_id)


def avisar_rh_nova_solicitacao(solicitacao):
    """Chamado ao criar o pedido. Envia depois de salvar, em segundo plano (o funcionário não espera o e-mail)."""
    if not email_configurado():
        return
    if not getattr(settings, "PROCESSAR_EM_SEGUNDO_PLANO", True):
        transaction.on_commit(lambda: _enviar(solicitacao.pk))
        return

    def rodar():
        try:
            _enviar(solicitacao.pk)
        finally:
            connection.close()

    transaction.on_commit(lambda: threading.Thread(target=rodar, daemon=True, name="aviso-email").start())
