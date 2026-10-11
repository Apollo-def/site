"""
Números que ainda não estão no cadastro: o assistente coleta nome e matrícula (PedidoCadastro) e o RH decide.
Liberar = gravar o celular no cadastro do funcionário certo; a partir daí o assistente reconhece a pessoa.
"""
import re

from django.db import transaction
from django.utils import timezone

from auditoria.models import LogAuditoria
from auditoria.servicos import registrar
from contas.models import Funcionario
from contas.validadores import formatar_celular, normalizar_celular, somente_digitos

from .canal import ErroCanal, canal_para
from .models import Conversa, Mensagem, PedidoCadastro
from .numeros import variantes


class ErroCadastro(Exception):
    pass


def nome_valido(texto):
    """Nome completo plausível: duas palavras ou mais, só letras (aceita acento, apóstrofo e hífen)."""
    nome = " ".join((texto or "").split())
    if not 5 <= len(nome) <= 150 or len(nome.split()) < 2:
        return ""
    if not re.fullmatch(r"[A-Za-zÀ-ÖØ-öø-ÿ' .-]+", nome):
        return ""
    if re.match(r"(bom dia|boa tarde|boa noite|oi|ola|olá|opa|tudo bem|obrigad\w*|valeu)\b", nome, re.IGNORECASE):
        return ""  # "Bom dia", "Bom dia tudo bem" não são nomes
    return nome


def matricula_informada(texto):
    digitos = somente_digitos(texto)
    return digitos if 2 <= len(digitos) <= 15 else ""


def liberar(pedido, funcionario, usuario, request=None):
    """O RH confirmou quem é: grava o celular no cadastro, avisa a pessoa e registra na auditoria."""
    if pedido.situacao != PedidoCadastro.Situacao.PENDENTE:
        raise ErroCadastro("Este pedido já foi resolvido.")
    if not pedido.cpf_informado:
        raise ErroCadastro("Este pedido ainda não tem o CPF da pessoa. Quando ela escrever de novo, o assistente pede o "
                           "CPF; aí dá para liberar.")
    if pedido.cpf_informado != funcionario.cpf:
        raise ErroCadastro(f"O CPF informado pela pessoa ({pedido.cpf_mascarado}) não é o de {funcionario.nome_completo}. "
                           "Não dá para liberar: escolha o funcionário que tem este CPF ou recuse o pedido.")
    if not normalizar_celular(pedido.numero):
        raise ErroCadastro(f"O número {pedido.numero} não parece um celular brasileiro válido. Confira com a pessoa "
                           "e cadastre o celular à mão no cadastro do funcionário.")
    outro = (Funcionario.objects.filter(whatsapp__in=variantes(pedido.numero)).exclude(pk=funcionario.pk).first())
    if outro:
        raise ErroCadastro(f"Este celular já está no cadastro de {outro.nome_completo} ({outro.matricula}). "
                           "Corrija o cadastro dela(e) antes de liberar.")
    with transaction.atomic():
        funcionario.telefone = formatar_celular(pedido.numero)
        funcionario.save(update_fields=["telefone", "atualizado_em"])
        pedido.situacao, pedido.funcionario = PedidoCadastro.Situacao.LIBERADO, funcionario
        pedido.resolvido_por, pedido.resolvido_em = usuario, timezone.now()
        pedido.save()
        conversa = pedido.conversa
        conversa.funcionario = funcionario
        conversa.estado = Conversa.Estado.ATENDIMENTO
        conversa.contexto = {"menu": "principal"}
        conversa.save(update_fields=["funcionario", "estado", "contexto", "atualizada_em"])
    registrar(LogAuditoria.Acao.WHATSAPP_CADASTRO_LIBERADO, request=request, usuario=usuario, objeto=funcionario,
              descricao=f"{funcionario.nome_completo} ({funcionario.matricula}): número "
                        f"{pedido.numero[:4]}*****{pedido.numero[-4:]} (informou: {pedido.nome_informado})")
    from .atendimento import Atendimento  # evita importação circular
    atendimento = Atendimento(conversa)
    texto = (f"✅ Pronto, {funcionario.primeiro_nome}! O RH conferiu os seus dados e liberou o seu acesso ao "
             "Assistente do Colaborador.\n\n"
             "A partir de agora você pode pedir por aqui, a qualquer hora, os seus contracheques, o termo de "
             "vale-transporte e o registro de empregado, e acompanhar os seus pedidos.\n\n"
             f"{atendimento.menu()}")
    return avisar(conversa, texto)


def recusar(pedido, usuario):
    pedido.situacao = PedidoCadastro.Situacao.RECUSADO
    pedido.resolvido_por, pedido.resolvido_em = usuario, timezone.now()
    pedido.save(update_fields=["situacao", "resolvido_por", "resolvido_em"])
    return avisar(pedido.conversa, "Não conseguimos confirmar o seu cadastro por aqui. Procure o seu supervisor "
                                   "ou o RH, por favor.")


def avisar(conversa, texto):
    mensagem = Mensagem(conversa=conversa, direcao=Mensagem.Direcao.SAIDA, autor=Mensagem.Autor.ASSISTENTE,
                        texto=texto)
    try:
        mensagem.id_externo = canal_para(conversa).enviar_aviso(conversa, texto)
    except ErroCanal:
        mensagem.falhou = True
    mensagem.save()
    return not mensagem.falhou
