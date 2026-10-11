"""Avisos que o sistema manda sozinho pelo WhatsApp (ex.: o RH respondeu o pedido)."""
from .canal import ErroCanal, canal_para
from .models import Conversa, Mensagem
from .numeros import variantes


def conversa_do_funcionario(funcionario):
    """
    A conversa mais recente do funcionário (a do simulador também, para os testes do RH), só se o número
    dela ainda for o celular do cadastro: se o RH trocou o celular, o aviso não vai para o número antigo.
    """
    if not funcionario.whatsapp:
        return None
    return (Conversa.objects.filter(funcionario=funcionario, numero__in=variantes(funcionario.whatsapp))
            .order_by("-ultima_entrada_em").first())


def avisar_mudanca_de_status(solicitacao):
    """Chamado pelo painel quando o RH muda a situação do pedido. Devolve True se o aviso saiu."""
    conversa = conversa_do_funcionario(solicitacao.funcionario)
    if conversa is None:
        return False
    texto = (f"Atualização do seu pedido {solicitacao.protocolo} ({solicitacao.tipo}): "
             f"{solicitacao.get_status_display()}.")
    if solicitacao.status == solicitacao.Status.RECUSADA and solicitacao.motivo_recusa:
        texto += f"\nMotivo: {solicitacao.motivo_recusa}\nSe quiser, é só me pedir o termo de novo."
    elif solicitacao.resposta_rh:
        texto += f"\nMensagem do RH: {solicitacao.resposta_rh}"
    if solicitacao.data_prevista_vigencia:
        texto += f"\nVale a partir de {solicitacao.data_prevista_vigencia:%d/%m/%Y}."
    mensagem = Mensagem(conversa=conversa, direcao=Mensagem.Direcao.SAIDA, autor=Mensagem.Autor.ASSISTENTE,
                        texto=texto)
    try:
        mensagem.id_externo = canal_para(conversa).enviar_aviso(conversa, texto)
    except ErroCanal:
        mensagem.falhou = True
    mensagem.save()
    return not mensagem.falhou
