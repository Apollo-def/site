"""
Regras dos pedidos, usadas pelo assistente do WhatsApp e pelo painel do RH: os dois criam
o pedido do mesmo jeito (protocolo, histórico, auditoria e aviso por e-mail ao RH).
"""
from django.db import transaction

from auditoria.models import LogAuditoria
from auditoria.servicos import registrar
from documentos.models import TipoDocumento

from .avisos import avisar_rh_nova_solicitacao
from .models import HistoricoSolicitacao, ItemFardamento, Solicitacao, TipoSolicitacao

NOMES_PADRAO_VT = {
    TipoSolicitacao.VT_UTILIZAR: "Opção / Reopção de Vale-Transporte",
    TipoSolicitacao.VT_CANCELAR: "Cancelamento de Vale-Transporte",
}
TAMANHO_MAXIMO_ANEXO_MB = 10
# Primeiros bytes de cada formato aceito: confere que o arquivo é mesmo PDF/foto, e não só o nome.
ASSINATURAS_ARQUIVO = {b"%PDF-": ".pdf", b"\xff\xd8\xff": ".jpg", b"\x89PNG\r\n\x1a\n": ".png"}


def tipos_vt_disponiveis(funcionario):
    """{código: tipo} dos pedidos de VT que este funcionário pode fazer (respeita 'ativo' e os contratos)."""
    tipos = {}
    for codigo, nome in NOMES_PADRAO_VT.items():
        tipo = TipoSolicitacao.objects.filter(codigo=codigo).first()
        if tipo is None:  # adota o tipo de mesmo nome já cadastrado ou cria na primeira vez
            tipo, _ = TipoSolicitacao.objects.get_or_create(nome=nome)
            tipo.codigo = codigo
            tipo.save(update_fields=["codigo"])
        disponivel = tipo.para_todos or tipo.contratos.filter(pk=funcionario.contrato_id).exists()
        if tipo.ativo and disponivel:
            tipos[codigo] = tipo
    return tipos


def vt_em_andamento(funcionario):
    """Pedido de VT ainda sem resposta (enquanto existir, não dá para mandar outro)."""
    return (funcionario.solicitacoes.pendentes()
            .filter(tipo__codigo__in=NOMES_PADRAO_VT).select_related("tipo").first())


def termo_vt_do_funcionario(funcionario):
    return funcionario.documentos.filter(tipo=TipoDocumento.TERMO_VT).order_by("-enviado_em").first()


def extensao_pelo_conteudo(inicio):
    """'.pdf', '.jpg' ou '.png' pelos primeiros bytes; None se não for nenhum deles."""
    for assinatura, extensao in ASSINATURAS_ARQUIVO.items():
        if inicio.startswith(assinatura):
            return extensao
    return None


def criar_solicitacao_vt(funcionario, tipo, arquivo, extensao, observacao="", origem=Solicitacao.Origem.WHATSAPP,
                         usuario=None, request=None):
    """Cria o pedido Pendente com o termo assinado anexado. `arquivo` é um File do Django."""
    with transaction.atomic():
        solicitacao = Solicitacao(funcionario=funcionario, tipo=tipo, observacao_funcionario=observacao,
                                  origem=origem)
        solicitacao.termo_assinado.save(f"termo_vt{extensao}", arquivo, save=False)
        solicitacao.save()
        canal = "pelo WhatsApp" if origem == Solicitacao.Origem.WHATSAPP else "pelo painel do RH"
        HistoricoSolicitacao.objects.create(solicitacao=solicitacao, status=solicitacao.status,
                                            observacao=f"Termo assinado enviado pelo funcionário {canal}.",
                                            usuario=usuario or funcionario.usuario)
    solicitacao.refresh_from_db()
    avisar_rh_nova_solicitacao(solicitacao)
    registrar(LogAuditoria.Acao.SOLICITACAO_CRIADA, request=request, usuario=usuario or funcionario.usuario,
              objeto=solicitacao, identificador=funcionario.matricula,
              descricao=f"{solicitacao.protocolo}: {solicitacao.tipo} ({canal})")
    return solicitacao


# ---------------------------------------------------------------------------
# Fardamento (camisa, calça, bota...): pedido pelo WhatsApp, uma linha por peça para o relatório do RH
# ---------------------------------------------------------------------------
NOME_PADRAO_FARDAMENTO = "Fardamento (uniforme)"


def tipo_fardamento_disponivel(funcionario):
    """O tipo "Fardamento" se este funcionário pode pedir (respeita 'ativo' e os contratos); senão None."""
    tipo = TipoSolicitacao.objects.filter(codigo=TipoSolicitacao.FARDAMENTO).first()
    if tipo is None:  # adota o tipo de mesmo nome já cadastrado ou cria na primeira vez
        tipo, _ = TipoSolicitacao.objects.get_or_create(nome=NOME_PADRAO_FARDAMENTO)
        tipo.codigo = TipoSolicitacao.FARDAMENTO
        tipo.save(update_fields=["codigo"])
    disponivel = tipo.para_todos or tipo.contratos.filter(pk=funcionario.contrato_id).exists()
    return tipo if tipo.ativo and disponivel else None


def fardamento_em_andamento(funcionario):
    return (funcionario.solicitacoes.pendentes().filter(tipo__codigo=TipoSolicitacao.FARDAMENTO)
            .select_related("tipo").first())


def criar_solicitacao_fardamento(funcionario, tipo, itens, motivo, origem=Solicitacao.Origem.WHATSAPP):
    """itens: [(peca, descricao, tamanho)]. Cria o pedido Pendente com as peças, histórico, auditoria e aviso."""
    resumo = ", ".join(f"{descricao or ItemFardamento.Peca(peca).label} {tamanho}" for peca, descricao, tamanho in itens)
    with transaction.atomic():
        solicitacao = Solicitacao.objects.create(funcionario=funcionario, tipo=tipo, origem=origem,
                                                 observacao_funcionario=f"Peças: {resumo}\nMotivo: {motivo}")
        ItemFardamento.objects.bulk_create([
            ItemFardamento(solicitacao=solicitacao, peca=peca, descricao=descricao[:60], tamanho=tamanho[:10])
            for peca, descricao, tamanho in itens
        ])
        HistoricoSolicitacao.objects.create(solicitacao=solicitacao, status=solicitacao.status,
                                            observacao="Pedido de fardamento feito pelo funcionário no WhatsApp.",
                                            usuario=funcionario.usuario)
    solicitacao.refresh_from_db()
    avisar_rh_nova_solicitacao(solicitacao)
    registrar(LogAuditoria.Acao.SOLICITACAO_CRIADA, usuario=funcionario.usuario, objeto=solicitacao,
              identificador=funcionario.matricula, descricao=f"{solicitacao.protocolo}: fardamento ({resumo})")
    return solicitacao
