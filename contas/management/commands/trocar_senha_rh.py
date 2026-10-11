"""
Troca a senha de um login do painel do RH. A senha vem da variável de ambiente NOVA_SENHA_RH
(o scripts/trocar_senha_rh.ps1 pede a senha com * e preenche a variável; ela nunca aparece na tela).

    python manage.py trocar_senha_rh grupo_global
"""
import os

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from contas.servicos import desbloquear

from .verificar_publicacao import SENHAS_PROIBIDAS


class Command(BaseCommand):
    help = "Troca a senha de um login do RH (senha na variável NOVA_SENHA_RH). Também destrava o login."

    def add_arguments(self, parser):
        parser.add_argument("usuario")

    def handle(self, *args, usuario, **options):
        senha = os.environ.get("NOVA_SENHA_RH", "")
        if not senha:
            raise CommandError("A senha não pode ficar vazia.")
        login = get_user_model().objects.filter(username=usuario, is_staff=True).first()
        if login is None:
            raise CommandError(f"Login do RH '{usuario}' não encontrado.")
        if senha.lower() in SENHAS_PROIBIDAS:
            raise CommandError("Senha recusada: é uma senha provisória ou conhecida. Escolha outra.")
        try:
            validate_password(senha, login)
        except ValidationError as erro:
            raise CommandError("Senha recusada: " + " ".join(erro.messages))
        login.set_password(senha)
        login.save()
        desbloquear(login)
        self.stdout.write(self.style.SUCCESS(
            f"Senha alterada! Entre no painel com o usuário '{login.username}' e a nova senha."))
