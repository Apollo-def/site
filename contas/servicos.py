"""Bloqueio do login do painel do RH (excesso de senhas erradas)."""
from django.utils import timezone


def bloquear(usuario):
    usuario.bloqueado = True
    usuario.bloqueado_em = timezone.now()
    usuario.save(update_fields=["bloqueado", "bloqueado_em", "tentativas_falhas"])


def desbloquear(usuario):
    usuario.bloqueado = False
    usuario.bloqueado_em = None
    usuario.tentativas_falhas = 0
    usuario.save(update_fields=["bloqueado", "bloqueado_em", "tentativas_falhas"])
