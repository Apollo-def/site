"""
Painel de acompanhamento do RH: quem já conversou com o assistente do WhatsApp, quem ainda não recebeu
o contracheque do mês e quais pedidos estão parados. Tudo calculado a partir do cadastro, das conversas e
do log de auditoria (cada contracheque enviado ou aberto pelo link fica registrado lá).
"""
from collections import defaultdict
from datetime import timedelta

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.template.response import TemplateResponse
from django.utils import timezone

from auditoria.models import LogAuditoria
from auditoria.servicos import registrar
from contas.models import Contrato, Funcionario
from contracheques.models import MESES, Contracheque, PaginaNaoIdentificada, TipoFolha
from documentos.models import PendenciaDocumento
from solicitacoes.models import Solicitacao
from whatsapp.models import Conversa, Mensagem

from .planilhas import resposta_excel

DIAS_PEDIDO_PARADO = 3
DIAS_GRAFICO_ACESSOS = 14
ACOES_ABRIU = [LogAuditoria.Acao.VISUALIZOU_DOCUMENTO, LogAuditoria.Acao.BAIXOU_DOCUMENTO,
               LogAuditoria.Acao.WHATSAPP_DOCUMENTO_ENVIADO]  # inclui o histórico do antigo site


def competencias_disponiveis():
    """[(2026, 8), (2026, 7), ...] da folha mensal, mais recente primeiro."""
    return list(Contracheque.objects.filter(tipo=TipoFolha.MENSAL).values_list("ano", "mes")
                .distinct().order_by("-ano", "-mes"))


def contracheques_abertos(contracheques):
    """Conjunto com o id dos contracheques que o funcionário já recebeu (WhatsApp) ou abriu."""
    ids = [str(c.pk) for c in contracheques]
    abertos = (LogAuditoria.objects
               .filter(objeto_tipo=str(Contracheque._meta.verbose_name), objeto_id__in=ids, acao__in=ACOES_ABRIU)
               .values_list("objeto_id", flat=True).distinct())
    return {int(i) for i in abertos}


def situacao_da_competencia(ano, mes):
    """Contracheques da folha mensal de ano/mes, separados em abertos e não abertos."""
    contracheques = list(Contracheque.objects.filter(ano=ano, mes=mes, tipo=TipoFolha.MENSAL)
                         .select_related("funcionario__contrato")
                         .order_by("funcionario__contrato__sigla", "funcionario__nome_completo"))
    abertos = contracheques_abertos(contracheques)
    nao_abriram = [c for c in contracheques if c.pk not in abertos]
    return contracheques, abertos, nao_abriram


def pedidos_parados():
    limite = timezone.now() - timedelta(days=DIAS_PEDIDO_PARADO)
    return (Solicitacao.objects.pendentes().filter(atualizado_em__lt=limite)
            .select_related("funcionario__contrato", "tipo").order_by("criado_em"))


def funcionarios_que_conversaram():
    """Ids dos funcionários que já falaram com o assistente pelo WhatsApp de verdade (fora o simulador)."""
    return set(Conversa.objects.filter(simulada=False, funcionario__isnull=False)
               .values_list("funcionario_id", flat=True))


def mensagens_por_dia():
    """[(data, mensagens recebidas no WhatsApp, % do maior dia)] dos últimos dias, incluindo dias sem mensagem."""
    hoje = timezone.localdate()
    inicio = hoje - timedelta(days=DIAS_GRAFICO_ACESSOS - 1)
    contagem = dict(
        Mensagem.objects
        .filter(direcao=Mensagem.Direcao.ENTRADA, conversa__simulada=False, criada_em__date__gte=inicio)
        .annotate(dia=TruncDate("criada_em", tzinfo=timezone.get_current_timezone()))
        .values("dia").annotate(total=Count("id")).values_list("dia", "total")
    )
    dias = [inicio + timedelta(days=i) for i in range(DIAS_GRAFICO_ACESSOS)]
    maior = max([contagem.get(d, 0) for d in dias] + [1])
    return [(d, contagem.get(d, 0), round(100 * contagem.get(d, 0) / maior)) for d in dias]


def por_contrato(contracheques, abertos, conversaram):
    """Linhas da tabela por contrato: ativos, com celular, já conversaram, contracheques publicados e recebidos."""
    linhas = defaultdict(lambda: {"ativos": 0, "com_celular": 0, "entraram": 0, "entregues": 0, "abertos": 0})
    ativos = Funcionario.objects.filter(situacao=Funcionario.Situacao.ATIVO).values_list(
        "pk", "contrato__sigla", "whatsapp")
    for pk, sigla, celular in ativos:
        linhas[sigla]["ativos"] += 1
        linhas[sigla]["com_celular"] += bool(celular)
        linhas[sigla]["entraram"] += pk in conversaram
    for c in contracheques:
        linhas[c.funcionario.contrato.sigla]["entregues"] += 1
        linhas[c.funcionario.contrato.sigla]["abertos"] += c.pk in abertos
    ordem = {sigla: i for i, sigla in enumerate(Contrato.objects.order_by("sigla").values_list("sigla", flat=True))}
    resultado = []
    for sigla, d in sorted(linhas.items(), key=lambda item: ordem.get(item[0], 999)):
        d["sigla"] = sigla
        d["pct_entraram"] = round(100 * d["entraram"] / d["ativos"]) if d["ativos"] else 0
        d["pct_abertos"] = round(100 * d["abertos"] / d["entregues"]) if d["entregues"] else 0
        resultado.append(d)
    return resultado


def exportar_nao_abriram(nao_abriram, ano, mes, conversaram):
    linhas = [[c.funcionario.nome_completo, c.funcionario.matricula, c.funcionario.cpf_formatado,
               c.funcionario.contrato.sigla, c.funcionario.cargo, c.funcionario.telefone,
               "Sim" if c.funcionario.whatsapp else "Não",
               "Sim" if c.funcionario_id in conversaram else "Não"]
              for c in nao_abriram]
    return resposta_excel(
        f"nao_receberam_contracheque_{ano}-{mes:02d}.xlsx", "Não receberam",
        ["Nome", "Matrícula", "CPF", "Contrato", "Cargo", "Telefone", "Celular válido no cadastro",
         "Já conversou no WhatsApp"],
        linhas)


def painel_acompanhamento(request):
    if not request.user.has_perm("contas.view_funcionario"):
        raise PermissionDenied
    competencias = competencias_disponiveis()
    escolhida = request.GET.get("competencia", "")
    ano, mes = competencias[0] if competencias else (None, None)
    if escolhida:
        try:
            pedido = tuple(int(p) for p in escolhida.split("-"))
            if pedido in competencias:
                ano, mes = pedido
        except ValueError:
            pass

    contracheques, abertos, nao_abriram = situacao_da_competencia(ano, mes) if ano else ([], set(), [])
    conversaram = funcionarios_que_conversaram()
    if ano and request.GET.get("exportar") == "nao_abriram":
        registrar(LogAuditoria.Acao.EXPORTOU_DADOS, request=request,
                  descricao=f"Quem não recebeu o contracheque de {mes:02d}/{ano} ({len(nao_abriram)} linhas)")
        return exportar_nao_abriram(nao_abriram, ano, mes, conversaram)

    ativos = Funcionario.objects.filter(situacao=Funcionario.Situacao.ATIVO)
    total_ativos = ativos.count()
    com_celular = ativos.exclude(whatsapp="").count()
    ja_entraram = ativos.filter(pk__in=conversaram).count()
    parados = list(pedidos_parados())
    contexto = {
        **admin.site.each_context(request),
        "title": "Painel de acompanhamento",
        "total_ativos": total_ativos,
        "ja_entraram": ja_entraram,
        "pct_entraram": round(100 * ja_entraram / total_ativos) if total_ativos else 0,
        "com_celular": com_celular,
        "pct_com_celular": round(100 * com_celular / total_ativos) if total_ativos else 0,
        "atendimentos_rh": Conversa.objects.filter(estado=Conversa.Estado.HUMANO, simulada=False).count(),
        "pedidos_abertos": Solicitacao.objects.pendentes().count(),
        "paginas_pendentes": (PaginaNaoIdentificada.objects.filter(resolvida=False).count()
                              + PendenciaDocumento.objects.filter(resolvida=False).count()),
        "competencias": [(f"{a}-{m:02d}", f"{dict(MESES)[m]} de {a}") for a, m in competencias],
        "competencia_atual": f"{ano}-{mes:02d}" if ano else "",
        "competencia_nome": f"{dict(MESES)[mes]} de {ano}" if ano else "",
        "entregues": len(contracheques),
        "abertos": len(abertos),
        "pct_abertos": round(100 * len(abertos) / len(contracheques)) if contracheques else 0,
        "nao_abriram": nao_abriram[:50],
        "total_nao_abriram": len(nao_abriram),
        "parados": parados,
        "dias_parado": DIAS_PEDIDO_PARADO,
        "acessos": mensagens_por_dia(),
        "por_contrato": por_contrato(contracheques, abertos, conversaram),
    }
    return TemplateResponse(request, "admin/acompanhamento.html", contexto)
