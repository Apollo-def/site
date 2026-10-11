"""
Separação automática do PDF mensal em contracheques individuais.

Fluxo:
  1. Lê página por página e descobre a matrícula (regras em extracao.py).
  2. Junta todas as páginas da mesma matrícula (contracheque de mais de uma página).
  3. Gera um PDF só daquele funcionário e disponibiliza na área dele.
  4. O que não der para identificar vira "página não identificada", para o RH vincular à mão.
Roda em segundo plano (thread) para o RH não ficar esperando com a tela travada.
"""
import io
from collections import Counter, OrderedDict

import pdfplumber
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from contas.models import Funcionario
from portal import lotes
from portal.lotes import descrever_paginas, juntar_pdfs, marcar_resolvida, montar_pdf, nomes_compativeis

from .extracao import ler_pagina, normalizar_matricula
from .models import MESES, Contracheque, LoteContracheque, PaginaNaoIdentificada, TipoFolha

Motivo = PaginaNaoIdentificada.Motivo


def iniciar_processamento(lote):
    """Chamado depois do upload. Em segundo plano no servidor; direto nos testes."""
    lotes.iniciar_processamento(lote, processar_lote)


def processar_lote(lote_id):
    lotes.processar_com_status(LoteContracheque, lote_id, _processar)


def nome_arquivo(contracheque_ou_lote):
    return f"contracheque_{contracheque_ou_lote.ano}-{contracheque_ou_lote.mes:02d}_{contracheque_ou_lote.tipo}.pdf"


def _processar(lote):
    conteudo, leitor = lotes.abrir_pdf_do_lote(lote)
    total = len(leitor.pages)
    LoteContracheque.objects.filter(pk=lote.pk).update(total_paginas=total, paginas_processadas=0)

    # 1) Leitura página por página
    por_matricula = OrderedDict()        # matrícula -> {"nome": ..., "paginas": [...]}
    problemas = []                       # (motivo, [páginas], matrícula, nome)
    competencias, tipos = Counter(), Counter()
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        for i, pagina in enumerate(pdf.pages):
            dados = ler_pagina(pagina.extract_text() or "")
            pagina.flush_cache()
            if dados.mes:
                competencias[(dados.mes, dados.ano)] += 1
            if dados.tipo:
                tipos[dados.tipo] += 1

            if not dados.tem_texto:
                problemas.append((Motivo.SEM_TEXTO, [i], "", ""))
            elif not dados.matriculas:
                problemas.append((Motivo.SEM_MATRICULA, [i], "", ""))
            elif len(dados.matriculas) > 1:
                problemas.append((Motivo.VARIAS_MATRICULAS, [i], ", ".join(dados.matriculas), ""))
            else:
                matricula = normalizar_matricula(dados.matriculas[0])
                grupo = por_matricula.setdefault(matricula, {"nome": dados.nomes[dados.matriculas[0]],
                                                             "lida": dados.matriculas[0], "paginas": []})
                grupo["paginas"].append(i)

            if (i + 1) % 5 == 0 or i + 1 == total:
                LoteContracheque.objects.filter(pk=lote.pk).update(paginas_processadas=i + 1)

    # 2) Conferências gerais
    avisos = []
    if competencias:
        (mes, ano), _ = competencias.most_common(1)[0]
        lote.competencia_detectada = f"{dict(MESES)[mes]} de {ano}"
        if (mes, ano) != (lote.mes, lote.ano):
            avisos.append(f"ATENÇÃO: o PDF parece ser de {lote.competencia_detectada}, mas o envio foi feito como "
                          f"{lote.competencia}. Se estiver errado, envie de novo com a competência correta.")
    if tipos:
        tipo_pdf = tipos.most_common(1)[0][0]
        if tipo_pdf != lote.tipo:
            avisos.append(f"ATENÇÃO: o PDF parece ser de '{TipoFolha(tipo_pdf).label}', mas o envio foi feito como "
                          f"'{lote.get_tipo_display()}'.")

    funcionarios = {normalizar_matricula(f.matricula): f for f in Funcionario.objects.select_related("contrato")}

    # 3) Gravação (tudo ou nada)
    distribuidos = 0
    with transaction.atomic():
        if lote.substituir_existentes:
            anteriores = (LoteContracheque.objects
                          .filter(ano=lote.ano, mes=lote.mes, tipo=lote.tipo, contrato=lote.contrato)
                          .exclude(pk=lote.pk).exclude(status=LoteContracheque.Status.SUBSTITUIDO))
            for antigo in Contracheque.objects.filter(lote__in=anteriores):
                antigo.delete()
            anteriores.update(status=LoteContracheque.Status.SUBSTITUIDO)

        for matricula, grupo in por_matricula.items():
            paginas = grupo["paginas"]
            funcionario = funcionarios.get(matricula)
            if funcionario is None:
                problemas.append((Motivo.NAO_CADASTRADA, paginas, grupo["lida"], grupo["nome"]))
                continue
            if not nomes_compativeis(grupo["nome"], funcionario.nome_completo):
                problemas.append((Motivo.NOME_DIVERGENTE, paginas, grupo["lida"], grupo["nome"]))
                continue
            if paginas != list(range(paginas[0], paginas[-1] + 1)):
                avisos.append(f"Matrícula {grupo['lida']} ({funcionario.nome_completo}) aparece em páginas separadas "
                              f"({descrever_paginas(paginas)}). As páginas foram juntadas num só contracheque.")
            if funcionario.situacao == Funcionario.Situacao.INATIVO:
                avisos.append(f"Matrícula {grupo['lida']} ({funcionario.nome_completo}) está como INATIVO no cadastro.")
            salvar_contracheque(lote, funcionario, montar_pdf(leitor, paginas), descrever_paginas(paginas))
            distribuidos += 1

        for motivo, paginas, matricula, nome in problemas:
            pendente = PaginaNaoIdentificada(lote=lote, paginas=descrever_paginas(paginas), motivo=motivo,
                                             matricula_lida=matricula[:30], nome_lido=nome[:200])
            pendente.arquivo.save(f"pendente_{lote.pk}.pdf", ContentFile(montar_pdf(leitor, paginas)), save=False)
            pendente.save()

        lote.status = LoteContracheque.Status.CONCLUIDO
        lote.concluido_em = timezone.now()
        lote.total_paginas = total
        lote.paginas_processadas = total
        lote.total_distribuidos = distribuidos
        lote.total_nao_encontrados = sum(1 for p in problemas if p[0] in (Motivo.NAO_CADASTRADA, Motivo.NOME_DIVERGENTE))
        lote.total_sem_matricula = sum(1 for p in problemas if p[0] not in (Motivo.NAO_CADASTRADA, Motivo.NOME_DIVERGENTE))
        lote.avisos = "\n".join(avisos)
        lote.save()


def salvar_contracheque(lote, funcionario, conteudo_pdf, paginas, manual=False, juntar=False):
    """Cria ou substitui o contracheque do funcionário nesta competência."""
    contracheque = Contracheque.objects.filter(funcionario=funcionario, ano=lote.ano, mes=lote.mes,
                                               tipo=lote.tipo).first()
    if contracheque is None:
        contracheque = Contracheque(funcionario=funcionario, ano=lote.ano, mes=lote.mes, tipo=lote.tipo)
    else:
        if juntar:  # vínculo manual de página de continuação: acrescenta ao que já existe
            with contracheque.arquivo.open("rb") as atual:
                conteudo_pdf = juntar_pdfs(atual.read(), conteudo_pdf)
            paginas = f"{contracheque.paginas}, {paginas}"
        nome_antigo, armazenamento = contracheque.arquivo.name, contracheque.arquivo.storage
        transaction.on_commit(lambda: armazenamento.delete(nome_antigo))
    contracheque.lote = lote
    contracheque.paginas = paginas[:50]
    contracheque.vinculado_manualmente = manual
    contracheque.arquivo.save(nome_arquivo(lote), ContentFile(conteudo_pdf), save=False)
    contracheque.save()
    return contracheque


def vincular_pendente(pendente, funcionario, usuario):
    """O RH escolheu manualmente a quem pertencem as páginas não identificadas."""
    with pendente.arquivo.open("rb") as arquivo:
        conteudo = arquivo.read()
    with transaction.atomic():
        ja_tinha = Contracheque.objects.filter(funcionario=funcionario, ano=pendente.lote.ano,
                                               mes=pendente.lote.mes, tipo=pendente.lote.tipo).exists()
        contracheque = salvar_contracheque(pendente.lote, funcionario, conteudo, pendente.paginas,
                                           manual=True, juntar=True)
        marcar_resolvida(pendente, funcionario, usuario)
        if not ja_tinha:
            LoteContracheque.objects.filter(pk=pendente.lote_id).update(total_distribuidos=F("total_distribuidos") + 1)
    return contracheque


def reentregar_pendencias(lote, usuario):
    """
    Depois que o RH cadastra quem faltava, tenta de novo entregar as páginas pendentes
    cuja matrícula agora existe no cadastro (com a mesma conferência de nome).
    Retorna (entregues, ainda_pendentes).
    """
    funcionarios = {normalizar_matricula(f.matricula): f for f in Funcionario.objects.all()}
    entregues = 0
    pendentes = lote.paginas_nao_identificadas.filter(resolvida=False, motivo=Motivo.NAO_CADASTRADA)
    for pendente in pendentes:
        funcionario = funcionarios.get(normalizar_matricula(pendente.matricula_lida))
        if funcionario and nomes_compativeis(pendente.nome_lido, funcionario.nome_completo):
            vincular_pendente(pendente, funcionario, usuario)
            entregues += 1
    ainda = lote.paginas_nao_identificadas.filter(resolvida=False).count()
    if entregues:
        LoteContracheque.objects.filter(pk=lote.pk).update(total_nao_encontrados=F("total_nao_encontrados") - entregues)
    return entregues, ainda


def descartar_pendente(pendente, usuario):
    """Página que não é de ninguém (capa, resumo da folha etc.)."""
    marcar_resolvida(pendente, None, usuario)
