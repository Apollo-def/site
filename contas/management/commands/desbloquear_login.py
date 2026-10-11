"""
Desbloqueia um login pelo terminal (útil se o único administrador ficar bloqueado).

    python manage.py desbloquear_login grupo_global
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from contas.servicos import desbloquear


class Command(BaseCommand):
    help = "Desbloqueia o login informado (nome de usuário do painel)."

    def add_arguments(self, parser):
        parser.add_argument("usuario")

    def handle(self, *args, **options):
        usuario = get_user_model().objects.filter(username=options["usuario"]).first()
        if usuario is None:
            raise CommandError("Usuário não encontrado.")
        desbloquear(usuario)
        self.stdout.write(self.style.SUCCESS(f"Login '{usuario.username}' desbloqueado."))
