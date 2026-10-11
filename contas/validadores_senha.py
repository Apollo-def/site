"""Regra das senhas do RH (vale ao trocar ou criar senha; senhas antigas fracas só geram aviso no painel)."""
from django.core.exceptions import ValidationError

from .autenticacao import TAMANHO_MINIMO_SENHA_RH, problemas_da_senha


class SenhaForteValidator:
    def validate(self, password, user=None):
        problemas = problemas_da_senha(password, user)
        if problemas:
            raise ValidationError("Senha fraca: " + "; ".join(problemas) + ".", code="senha_fraca")

    def get_help_text(self):
        return (f"Use {TAMANHO_MINIMO_SENHA_RH} caracteres ou mais, misturando minúsculas, MAIÚSCULAS, números e "
                "símbolos, sem o nome da empresa ou do usuário. Dica: uma frase, como Cafe-Com-Pao-As-7h.")
