"""
Relatório detalhado das ocorrências do grupo (faltas, atestados, coberturas e extras) num período:
totais, por funcionário, por escola/posto, por dia e por supervisor. A mesma conta alimenta a tela e o Excel.
"""
from collections import defaultdict
from datetime import date

from django.db.models import Q

from .leitura import consertar
from .models import Ocorrencia

TIPOS = [valor for valor, _ in Ocorrencia.Tipo.choices]


def dias_de_atestado(ocorrencia):
    """Dias do atestado (de/até, contando os dois); 1 quando o período não veio na mensagem."""
    if ocorrencia.tipo != Ocorrencia.Tipo.ATESTADO:
        return 0
    if ocorrencia.atestado_inicio and ocorrencia.atestado_fim and ocorrencia.atestado_fim >= ocorrencia.atestado_inicio:
        return (ocorrencia.atestado_fim - ocorrencia.atestado_inicio).days + 1
    return 1


def escola_de(ocorrencia):
    """Onde a ocorrência aconteceu: o posto escrito pelo supervisor; sem ele, a escola do cadastro."""
    if ocorrencia.escola_informada.strip():
        return " ".join(ocorrencia.escola_informada.split()).upper()
    if ocorrencia.funcionario_id and ocorrencia.funcionario.instalacao:
        return consertar(ocorrencia.funcionario.instalacao)
    return "(escola não informada)"


def _zerado():
    return {**{tipo: 0 for tipo in TIPOS}, "dias_atestado": 0, "coberto": 0, "total": 0}


def filtrar(de, ate, tipo="", contrato="", escola=""):
    ocorrencias = (Ocorrencia.objects.filter(data__gte=de, data__lte=ate)
                   .select_related("funcionario__contrato", "coberto").order_by("data", "recebida_em"))
    if tipo:
        ocorrencias = ocorrencias.filter(tipo=tipo)
    if contrato:
        ocorrencias = ocorrencias.filter(funcionario__contrato_id=contrato)
    if escola:
        ocorrencias = ocorrencias.filter(Q(escola_informada__icontains=escola) |
                                         Q(funcionario__instalacao__icontains=escola))
    return ocorrencias


def montar(ocorrencias):
    totais = _zerado()
    por_funcionario = {}
    por_escola, por_dia, por_supervisor = defaultdict(_zerado), defaultdict(_zerado), defaultdict(_zerado)
    a_conferir = sem_ok_adm = 0

    def pessoa(funcionario, nome_informado, escola):
        chave = funcionario.pk if funcionario else f"?{nome_informado.upper()}"
        if chave not in por_funcionario:
            por_funcionario[chave] = {
                **_zerado(), "nome": funcionario.nome_completo if funcionario else f"{nome_informado} (conferir)",
                "matricula": funcionario.matricula if funcionario else "",
                "contrato": funcionario.contrato.sigla if funcionario else "",
                "escola": consertar(funcionario.instalacao) if funcionario and funcionario.instalacao else escola,
                "conferir": funcionario is None,
            }
        return por_funcionario[chave]

    for o in ocorrencias:
        dias = dias_de_atestado(o)
        escola = escola_de(o)
        linhas = [totais, pessoa(o.funcionario, o.nome_informado, escola), por_escola[escola], por_dia[o.data],
                  por_supervisor[o.supervisor_nome or "(sem nome)"]]
        for linha in linhas:
            if o.tipo:
                linha[o.tipo] += 1
            linha["dias_atestado"] += dias
            linha["total"] += 1
        if o.coberto_id or o.coberto_nome_informado:
            pessoa(o.coberto, o.coberto_nome_informado, escola)["coberto"] += 1
        a_conferir += o.situacao == Ocorrencia.Situacao.CONFERIR
        sem_ok_adm += o.ok_adm_em is None

    def mais_ocorrencias_primeiro(par):
        return -par[1]["total"], str(par[0])

    funcionarios = sorted(por_funcionario.values(), key=lambda p: (-(p["total"] + p["coberto"]), p["nome"]))
    return {
        "totais": totais,
        "a_conferir": a_conferir,
        "sem_ok_adm": sem_ok_adm,
        "por_funcionario": funcionarios,
        "por_escola": [{"escola": escola, **linha}
                       for escola, linha in sorted(por_escola.items(), key=mais_ocorrencias_primeiro)],
        "por_dia": [{"data": dia, **linha} for dia, linha in sorted(por_dia.items())],
        "por_supervisor": [{"supervisor": nome, **linha}
                           for nome, linha in sorted(por_supervisor.items(), key=mais_ocorrencias_primeiro)],
    }


def abas_excel(ocorrencias, dados):
    """As abas do Excel: o detalhado (uma linha por ocorrência) e os resumos."""
    rotulo = dict(Ocorrencia.Tipo.choices)
    detalhado = [[
        o.data, rotulo.get(o.tipo, "?"),
        o.funcionario.nome_completo if o.funcionario_id else f"{o.nome_informado} (conferir)",
        o.funcionario.matricula if o.funcionario_id else "", escola_de(o),
        o.coberto.nome_completo if o.coberto_id else o.coberto_nome_informado,
        o.coberto.matricula if o.coberto_id else "", o.atestado_inicio, o.atestado_fim, dias_de_atestado(o) or "",
        o.observacao, o.supervisor_nome, o.ok_adm_em, o.get_situacao_display(), o.recebida_em, o.texto_original,
    ] for o in ocorrencias]
    contagens = ["Faltas", "Atestados", "Dias de atestado", "Coberturas", "Extras"]

    def numeros(linha):
        return [linha["falta"], linha["atestado"], linha["dias_atestado"], linha["cobertura"], linha["extra"]]

    return [
        ("Detalhado", ["Data", "Tipo", "Funcionário", "Matrícula", "Escola/posto", "Cobrindo", "Matrícula coberto",
                       "Atestado de", "Atestado até", "Dias de atestado", "Observação", "Enviada por",
                       "OK do ADM (👍) em", "Situação", "Recebida em", "Mensagem original"], detalhado),
        ("Por funcionário", ["Funcionário", "Matrícula", "Contrato", "Escola/posto", *contagens, "Vezes coberto(a)",
                             "Total"],
         [[p["nome"], p["matricula"], p["contrato"], p["escola"], *numeros(p), p["coberto"], p["total"]]
          for p in dados["por_funcionario"]]),
        ("Por escola", ["Escola/posto", *contagens, "Total"],
         [[e["escola"], *numeros(e), e["total"]] for e in dados["por_escola"]]),
        ("Por dia", ["Data", *contagens, "Total"], [[d["data"], *numeros(d), d["total"]] for d in dados["por_dia"]]),
        ("Por supervisor", ["Supervisor", *contagens, "Total"],
         [[s["supervisor"], *numeros(s), s["total"]] for s in dados["por_supervisor"]]),
    ]


def periodo_padrao(hoje):
    """Do dia 1º do mês até hoje."""
    return hoje.replace(day=1), hoje


def ler_data(texto, padrao):
    try:
        return date.fromisoformat(texto) if texto else padrao
    except ValueError:
        return padrao
