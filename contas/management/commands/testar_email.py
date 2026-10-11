"""
Manda um e-mail de teste para os endereços de EMAILS_AVISO_RH (confere a configuração de e-mail do .env).

    python manage.py testar_email
"""
from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand, CommandError

from solicitacoes.avisos import email_configurado


class Command(BaseCommand):
    help = "Envia um e-mail de teste para os endereços de aviso do RH."

    def handle(self, *args, **options):
        if not email_configurado():
            raise CommandError("E-mail não configurado: preencha EMAIL_HOST, EMAIL_HOST_USER, EMAIL_HOST_PASSWORD "
                               "e EMAILS_AVISO_RH no arquivo .env.")
        try:
            send_mail("Teste do Portal do Colaborador",
                      "Se você recebeu este e-mail, os avisos de novos pedidos estão funcionando.",
                      None, settings.EMAILS_AVISO_RH)
        except Exception as erro:
            raise CommandError(f"Não consegui enviar: {erro}")
        self.stdout.write(self.style.SUCCESS("E-mail de teste enviado para: " + ", ".join(settings.EMAILS_AVISO_RH)))
