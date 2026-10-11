"""
Separação automática de um PDF com o mesmo documento de todos os funcionários
(Termo de opção de VT, Registro de empregado...) em documentos individuais.

Fluxo (igual ao dos contracheques, mas a chave aqui é o CPF):
  1. Lê página por página e descobre o CPF.
  2. Junta as páginas do mesmo CPF.
  3. Gera um PDF só daquele funcionário e disponibiliza na área dele.
     Se ele já tinha um documento desse tipo vindo de um envio anterior, o antigo é substituído.
  4. O que não der para identificar vira pendência, para o RH vincular à mão.

LAYOUTS CONFERIDOS (setembro/2026):
  - Termo de opção de VT: "Eu, NOME. portador(a) do CPF: 000.000.000-00 ..." e, na assinatura, "NOME / CPF: ...".
  - Registro de empregado: nome logo abaixo de "Empregado Beneficiários"; CPF sem rótulo, no formato 000.000.000-00.
"""
import io
import re
from collections import OrderedDict

import pdfplumber
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from contas.models import Funcionario
from contas.validadores import somente_digitos
from portal import lotes
from portal.lotes import (MINIMO_CARACTERES_TEXTO, descrever_paginas, juntar_pdfs, marcar_resolvida, montar_pdf,
                          nomes_compativeis)

from .models import DocumentoFuncionario, LoteDocumento, PendenciaDocumento

Motivo = PendenciaDocumento.Motivo

# CPF formatado em qualquer lugar da página, ou só dígitos logo depois de "CPF".
REGRA_CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b|(?<=CPF)[:\s]*\d{11}\b")
REGRAS_NOME = [
    re.compile(r"\bEu,\s*(?P<nome>[^\n]+?)\.?\s+portador", re.I),
    re.compile(r"Empregado\s+Benefici[áa]rios\s*\n(?P<nome>[^\n]+)", re.I),
    re.compile(r"^(?P<nome>[A-ZÀ-Ü][A-ZÀ-Ü ]{4,})\s*\n\s*CPF:", re.M),
]


def ler_pagina(texto):
    """Devolve (tem_texto, [CPFs diferentes na página], nome lido ou '')."""
    texto = texto or ""
    if len(texto.strip()) < MINIMO_CARACTERES_TEXTO:
        return False, [], ""
    cpfs = []
    for achado in REGRA_CPF.findall(texto):
        cpf = somente_digitos(achado)
        if cpf not in cpfs:
            cpfs.append(cpf)
    nome = ""
    for regra in REGRAS_NOME:
        achado = regra.search(texto)
        if achado:
            nome = " ".join(achado.group("nome").split())
            break
    return True, cpfs, nome


def iniciar_processamento(lote):
    """Chamado depois do upload. Em segundo plano no servidor; direto nos testes."""
    lotes.iniciar_processamento(lote, processar_lote)


def processar_lote(lote_id):
    lotes.processar_com_status(LoteDocumento, lote_id, _processar)


def _processar(lote):
    conteudo, leitor = lotes.abrir_pdf_do_lote(lote)
    total = len(leitor.pages)
    LoteDocumento.objects.filter(pk=lote.pk).update(total_paginas=total, paginas_processadas=0)

    # 1) Leitura página por página
    por_cpf = OrderedDict()   # cpf -> {"nome": ..., "paginas": [...]}
    problemas = []            # (motivo, [páginas], cpf, nome)
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        for i, pagina in enumerate(pdf.pages):
            tem_texto, cpfs, nome = ler_pagina(pagina.extract_text())
            pagina.flush_cache()
            if not tem_texto:
                problemas.append((Motivo.SEM_TEXTO, [i], "", ""))
            elif not cpfs:
                problemas.append((Motivo.SEM_CPF, [i], "", nome))
            elif len(cpfs) > 1:
                problemas.append((Motivo.VARIOS_CPFS, [i], "", nome))
            else:
                grupo = por_cpf.setdefault(cpfs[0], {"nome": nome, "paginas": []})
                grupo["paginas"].append(i)
            if (i + 1) % 10 == 0 or i + 1 == total:
                LoteDocumento.objects.filter(pk=lote.pk).update(paginas_processadas=i + 1)

    funcionarios = {f.cpf: f for f in Funcionario.objects.all()}

    # 2) Gravação (tudo ou nada)
    avisos, distribuidos = [], 0
    with transaction.atomic():
        for cpf, grupo in por_cpf.items():
            paginas = grupo["paginas"]
            funcionario = funcionarios.get(cpf)
            if funcionario is None:
                problemas.append((Motivo.NAO_CADASTRADO, paginas, cpf, grupo["nome"]))
                continue
            if grupo["nome"] and not nomes_compativeis(grupo["nome"], funcionario.nome_completo):
                problemas.append((Motivo.NOME_DIVERGENTE, paginas, cpf, grupo["nome"]))
                continue
            if paginas != list(range(paginas[0], paginas[-1] + 1)):
                avisos.append(f"CPF de {funcionario.nome_completo} aparece em páginas separadas "
                              f"({descrever_paginas(paginas)}). As páginas foram juntadas num só documento.")
            salvar_documento(lote, funcionario, montar_pdf(leitor, paginas), descrever_paginas(paginas))
            distribuidos += 1

        for motivo, paginas, cpf, nome in problemas:
            pendencia = PendenciaDocumento(lote=lote, paginas=descrever_paginas(paginas), motivo=motivo,
                                           cpf_lido=cpf[:14], nome_lido=nome[:200])
            pendencia.arquivo.save(f"pendente_{lote.pk}.pdf", ContentFile(montar_pdf(leitor, paginas)), save=False)
            pendencia.save()

        lote.status = LoteDocumento.Status.CONCLUIDO
        lote.concluido_em = timezone.now()
        lote.total_paginas = total
        lote.paginas_processadas = total
        lote.total_distribuidos = distribuidos
        lote.total_nao_encontrados = sum(1 for p in problemas if p[0] in (Motivo.NAO_CADASTRADO, Motivo.NOME_DIVERGENTE))
        lote.total_sem_cpf = sum(1 for p in problemas if p[0] not in (Motivo.NAO_CADASTRADO, Motivo.NOME_DIVERGENTE))
        lote.avisos = "\n".join(avisos)
        lote.save()


def salvar_documento(lote, funcionario, conteudo_pdf, paginas, manual=False, juntar=False):
    """
    Entrega o documento ao funcionário. Cada funcionário fica com UM documento de cada tipo vindo
    de envio em lote: um envio novo substitui o anterior (ex.: termo de VT atualizado).
    """
    documento = (DocumentoFuncionario.objects
                 .filter(funcionario=funcionario, tipo=lote.tipo, lote__isnull=False)
                 .order_by("-enviado_em").first())
    if documento is None:
        documento = DocumentoFuncionario(funcionario=funcionario, tipo=lote.tipo)
    else:
        if juntar and documento.lote_id == lote.pk:  # página de continuação vinculada à mão
            with documento.arquivo.open("rb") as atual:
                conteudo_pdf = juntar_pdfs(atual.read(), conteudo_pdf)
            paginas = f"{documento.paginas}, {paginas}"
        nome_antigo, armazenamento = documento.arquivo.name, documento.arquivo.storage
        transaction.on_commit(lambda: armazenamento.delete(nome_antigo))
        documento.enviado_em = timezone.now()
    documento.titulo = lote.titulo
    documento.lote = lote
    documento.paginas = paginas[:50]
    documento.vinculado_manualmente = manual
    documento.enviado_por = lote.enviado_por
    documento.arquivo.save(f"{lote.tipo}.pdf", ContentFile(conteudo_pdf), save=False)
    documento.save()
    return documento


def vincular_pendencia(pendencia, funcionario, usuario):
    """O RH escolheu manualmente a quem pertencem as páginas não identificadas."""
    with pendencia.arquivo.open("rb") as arquivo:
        conteudo = arquivo.read()
    with transaction.atomic():
        ja_tinha = DocumentoFuncionario.objects.filter(funcionario=funcionario, lote=pendencia.lote).exists()
        documento = salvar_documento(pendencia.lote, funcionario, conteudo, pendencia.paginas, manual=True, juntar=True)
        marcar_resolvida(pendencia, funcionario, usuario)
        if not ja_tinha:
            LoteDocumento.objects.filter(pk=pendencia.lote_id).update(total_distribuidos=F("total_distribuidos") + 1)
    return documento


def reentregar_pendencias(lote, usuario):
    """
    Depois que o RH cadastra quem faltava, entrega de novo as páginas cujo CPF agora existe.
    Retorna (entregues, ainda_pendentes).
    """
    funcionarios = {f.cpf: f for f in Funcionario.objects.all()}
    entregues = 0
    for pendencia in lote.pendencias.filter(resolvida=False, motivo=Motivo.NAO_CADASTRADO):
        funcionario = funcionarios.get(pendencia.cpf_lido)
        if funcionario and (not pendencia.nome_lido or nomes_compativeis(pendencia.nome_lido, funcionario.nome_completo)):
            vincular_pendencia(pendencia, funcionario, usuario)
            entregues += 1
    if entregues:
        LoteDocumento.objects.filter(pk=lote.pk).update(total_nao_encontrados=F("total_nao_encontrados") - entregues)
    return entregues, lote.pendencias.filter(resolvida=False).count()


def descartar_pendencia(pendencia, usuario):
    """Página que não é de ninguém (capa, página em branco etc.)."""
    marcar_resolvida(pendencia, None, usuario)
