"""
Emergência: desativa a verificação em duas etapas de um login do RH (perdeu o celular e os códigos reserva).
Só funciona no próprio PC servidor, que já exige acesso físico/administrador.

    python manage.py desativar_duas_etapas grupo_global
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from auditoria.models import LogAuditoria
from auditoria.servicos import registrar


class Command(BaseCommand):
    help = "Desativa a verificação em duas etapas de um login do RH (perdeu o celular)."

    def add_arguments(self, parser):
        parser.add_argument("usuario")

    def handle(self, *args, usuario, **options):
        login = get_user_model().objects.filter(username=usuario, is_staff=True).first()
        if login is None:
            raise CommandError(f"Login do RH '{usuario}' não encontrado.")
        login.duas_etapas_ativa, login.duas_etapas_segredo, login.duas_etapas_reserva = False, "", []
        login.save(update_fields=["duas_etapas_ativa", "duas_etapas_segredo", "duas_etapas_reserva"])
        registrar(LogAuditoria.Acao.DUAS_ETAPAS_DESATIVADA, usuario=login, descricao="Pelo comando de emergência")
        self.stdout.write(self.style.SUCCESS(f"Verificação em duas etapas desativada para {usuario}. "
                                             "Entre com a senha e ative de novo no painel."))
