"""Auditoria dos logins feitos pelo painel do RH (/admin/) e das saídas do sistema."""
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from auditoria.models import LogAuditoria
from auditoria.servicos import obter_ip, registrar

from .autenticacao import contar_falha_ip, ip_barrado, problemas_da_senha
from .middleware import CHAVE_VERIFICADO
from .models import ConfiguracaoPortal
from .servicos import bloquear


@receiver(user_logged_in)
def login_painel(sender, request, user, **kwargs):
    if user.is_staff:
        if user.tentativas_falhas:
            user.tentativas_falhas = 0
            user.save(update_fields=["tentativas_falhas"])
        registrar(LogAuditoria.Acao.LOGIN, request=request, usuario=user, descricao="Painel do RH")
        if request is not None:
            request.session.pop(CHAVE_VERIFICADO, None)  # cada login novo pede o código do celular de novo
        # Só no login o sistema vê a senha antes de ela virar código: se for fraca, o painel avisa até trocar.
        senha = request.POST.get("password", "") if request is not None and request.method == "POST" else ""
        if senha and problemas_da_senha(senha, user):
            # Guarda junto um pedaço do código da senha: trocou a senha (por qualquer caminho), o aviso some.
            request.session["senha_fraca"] = {"problemas": problemas_da_senha(senha, user), "senha": user.password[-16:]}


@receiver(user_login_failed)
def falha_login_painel(sender, credentials, request=None, **kwargs):
    nome = (credentials or {}).get("username") or ""
    ip = (obter_ip(request) if request is not None else None) or "desconhecido"
    if ip_barrado(ip):
        # IP já barrado: a senha nem foi conferida, então não conta como erro do login (não trava a conta).
        registrar(LogAuditoria.Acao.LOGIN_FALHA, request=request, identificador=nome,
                  descricao="Painel do RH: barrado por excesso de tentativas deste IP")
        return
    contar_falha_ip(ip)
    usuario = get_user_model().objects.filter(username=nome, is_staff=True).first() if nome else None
    if usuario is None:
        registrar(LogAuditoria.Acao.LOGIN_FALHA, request=request, identificador=nome, descricao="Painel do RH")
        return
    if usuario.bloqueado:
        registrar(LogAuditoria.Acao.LOGIN_FALHA, request=request, usuario=usuario,
                  descricao="Painel do RH: tentativa com login travado")
        return
    usuario.tentativas_falhas += 1
    maximo = ConfiguracaoPortal.atual().maximo_tentativas
    registrar(LogAuditoria.Acao.LOGIN_FALHA, request=request, usuario=usuario,
              descricao=f"Painel do RH: tentativa errada {usuario.tentativas_falhas} de {maximo}")
    if usuario.tentativas_falhas >= maximo:
        bloquear(usuario)
        registrar(LogAuditoria.Acao.CONTA_BLOQUEADA, request=request, usuario=usuario,
                  descricao="Painel do RH: travado por 15 minutos")
    else:
        usuario.save(update_fields=["tentativas_falhas"])


@receiver(user_logged_out)
def saida(sender, request, user, **kwargs):
    if user is not None:
        identificador = user.funcionario.matricula if hasattr(user, "funcionario") else user.get_username()
        registrar(LogAuditoria.Acao.LOGOUT, request=request, usuario=user, identificador=identificador)
