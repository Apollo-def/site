"""
Login do painel do RH (usuário e senha). Os funcionários não têm login: são atendidos pelo WhatsApp,
identificados pelo celular do cadastro.

Proteções:
  - Após X tentativas erradas (padrão 5, Configuração do portal) o login trava por 15 minutos.
  - Muitas tentativas erradas vindas do mesmo IP são barradas por 15 minutos.
  - Tudo é registrado no log de auditoria (sinais.py).
"""
from datetime import timedelta

from django.contrib.auth.backends import ModelBackend
from django.core.cache import cache
from django.utils import timezone

from auditoria.servicos import obter_ip

from .servicos import desbloquear

MINUTOS_BLOQUEIO_ADMIN = 15
TAMANHO_MINIMO_SENHA_RH = 10
PALAVRAS_PREVISIVEIS = ("global", "grupo", "globaltech", "senha", "admin", "portal", "rh", "123", "2025", "2026")


def problemas_da_senha(senha, usuario=None):
    """Lista do que deixa a senha fraca ([] = boa). Para logins do RH, que abrem os dados de todos."""
    problemas = []
    if len(senha) < TAMANHO_MINIMO_SENHA_RH:
        problemas.append(f"tem menos de {TAMANHO_MINIMO_SENHA_RH} caracteres")
    tipos = sum([any(c.islower() for c in senha), any(c.isupper() for c in senha),
                 any(c.isdigit() for c in senha), any(not c.isalnum() for c in senha)])
    if tipos < 3:
        problemas.append("precisa misturar pelo menos 3 destes: minúsculas, MAIÚSCULAS, números e símbolos")
    minuscula = senha.lower()
    nome = (usuario.get_username().lower() if usuario is not None else "")
    previsiveis = [p for p in PALAVRAS_PREVISIVEIS if p in minuscula]
    if nome and len(nome) >= 3 and (nome in minuscula or any(parte in minuscula for parte in nome.split("_")
                                                             if len(parte) >= 4)):
        previsiveis.append("o nome do usuário")
    if previsiveis:
        problemas.append("usa palavras fáceis de adivinhar (" + ", ".join(dict.fromkeys(previsiveis)) + ")")
    return problemas


LIMITE_FALHAS_POR_IP = 30
JANELA_FALHAS_IP = 15 * 60


class BackendPortal(ModelBackend):
    """Igual ao padrão do Django, mas não deixa entrar quem está bloqueado nem quem vem de IP barrado."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        # Limite por IP: sem ele, dava para testar senhas em vários logins diferentes
        # (o bloqueio por conta só conta os erros de cada login).
        if request is not None and ip_barrado(obter_ip(request) or "desconhecido"):
            return None
        return super().authenticate(request, username=username, password=password, **kwargs)

    def user_can_authenticate(self, user):
        if not super().user_can_authenticate(user):
            return False
        if getattr(user, "bloqueado", False):
            limite = timezone.now() - timedelta(minutes=MINUTOS_BLOQUEIO_ADMIN)
            if user.is_staff and user.bloqueado_em and user.bloqueado_em < limite:
                desbloquear(user)  # administrador: libera sozinho após 15 minutos
                return True
            return False
        return True


def _chave_ip(ip):
    return f"login_falhas_ip:{ip}"


def ip_barrado(ip):
    return cache.get(_chave_ip(ip), 0) >= LIMITE_FALHAS_POR_IP


def contar_falha_ip(ip):
    chave = _chave_ip(ip)
    if not cache.add(chave, 1, JANELA_FALHAS_IP):
        try:
            cache.incr(chave)
        except ValueError:
            cache.set(chave, 1, JANELA_FALHAS_IP)
