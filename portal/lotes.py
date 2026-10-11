"""
Peças comuns dos envios em lote (um PDF com todos os funcionários, separado página por página):
contracheques (pela matrícula) e documentos como termo de VT e registro de empregado (pelo CPF).

Cada módulo faz a própria leitura das páginas; aqui fica o que é igual para todos:
montar PDFs, rodar em segundo plano, marcar erro e resolver pendências.
"""
import io
import logging
import threading
import time
import unicodedata

from django import forms
from django.conf import settings
from django.db import OperationalError, connection, transaction
from django.utils import timezone
from pypdf import PdfReader, PdfWriter

logger = logging.getLogger(__name__)

TAMANHO_MAXIMO_PDF_MB = 100
MINIMO_CARACTERES_TEXTO = 30  # página com menos texto que isso = provavelmente escaneada (imagem)
# Um PDF por vez: o SQLite grava uma coisa de cada vez, e a gravação final de um lote grande demora.
# Dois lotes juntos davam "database is locked" (ou o segundo parava no meio).
FILA_DE_PROCESSAMENTO = threading.Lock()
TENTATIVAS_BANCO_OCUPADO = 3
ESPERA_BANCO_OCUPADO_SEGUNDOS = 30


# ---------------------------------------------------------------------------
# Upload e leitura
# ---------------------------------------------------------------------------
def validar_pdf_enviado(arquivo):
    """Usado no clean_arquivo dos formulários de envio: confere tamanho e se é mesmo um PDF."""
    if arquivo.size > TAMANHO_MAXIMO_PDF_MB * 1024 * 1024:
        raise forms.ValidationError(f"Arquivo muito grande (máximo {TAMANHO_MAXIMO_PDF_MB} MB).")
    if arquivo.read(5) != b"%PDF-":
        raise forms.ValidationError("Este arquivo não é um PDF válido.")
    arquivo.seek(0)
    return arquivo


def sem_acento(texto):
    return unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()


def normalizar_nome(nome):
    return " ".join(sem_acento(nome).upper().split())


def nomes_compativeis(nome_pdf, nome_cadastro):
    """
    Conferência secundária (a chave é a matrícula ou o CPF). Aceita diferenças pequenas
    (abreviação, acento, nome do meio), mas recusa quando primeiro E último nome diferem.
    """
    pdf, cadastro = normalizar_nome(nome_pdf).split(), normalizar_nome(nome_cadastro).split()
    if not pdf or not cadastro:
        return True
    return pdf[0] == cadastro[0] or pdf[-1] == cadastro[-1]


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def descrever_paginas(indices):
    """[0, 1, 2, 5] -> '1-3, 6' (numeração de páginas começando em 1)."""
    numeros = [i + 1 for i in indices]
    faixas, inicio = [], numeros[0]
    for anterior, atual in zip(numeros, numeros[1:] + [None]):
        if atual != anterior + 1:
            faixas.append(str(inicio) if inicio == anterior else f"{inicio}-{anterior}")
            inicio = atual
    return ", ".join(faixas)


def montar_pdf(leitor, indices):
    escritor = PdfWriter()
    for i in indices:
        escritor.add_page(leitor.pages[i])
    saida = io.BytesIO()
    escritor.write(saida)
    return saida.getvalue()


def juntar_pdfs(*conteudos):
    escritor = PdfWriter()
    for conteudo in conteudos:
        for pagina in PdfReader(io.BytesIO(conteudo)).pages:
            escritor.add_page(pagina)
    saida = io.BytesIO()
    escritor.write(saida)
    return saida.getvalue()


def abrir_pdf_do_lote(lote):
    """Devolve (bytes do PDF, leitor). Recusa PDF protegido por senha."""
    with lote.arquivo.open("rb") as arquivo:
        conteudo = arquivo.read()
    leitor = PdfReader(io.BytesIO(conteudo))
    if leitor.is_encrypted:
        raise ValueError("o PDF está protegido por senha. Gere o arquivo sem senha e envie de novo.")
    return conteudo, leitor


# ---------------------------------------------------------------------------
# Processamento em segundo plano
# ---------------------------------------------------------------------------
def iniciar_processamento(lote, processar):
    """
    Chamado depois do upload: roda processar(lote.pk) numa thread (o RH não fica com a tela travada).
    Nos testes (PROCESSAR_EM_SEGUNDO_PLANO=False) roda direto.
    """
    if not getattr(settings, "PROCESSAR_EM_SEGUNDO_PLANO", True):
        transaction.on_commit(lambda: processar(lote.pk))
        return

    def rodar():
        try:
            with FILA_DE_PROCESSAMENTO:  # o próximo PDF espera como "Aguardando processamento"
                processar(lote.pk)
        finally:
            connection.close()

    nome = f"{lote._meta.model_name}-{lote.pk}"
    transaction.on_commit(lambda: threading.Thread(target=rodar, daemon=True, name=nome).start())


def processar_com_status(modelo, lote_id, etapas):
    """
    Marca o lote como 'processando', roda etapas(lote) e, se algo falhar, marca 'erro' com o motivo.
    Banco ocupado por outra gravação grande ("database is locked", no SQLite): espera e tenta de novo.
    Como a gravação é tudo ou nada, repetir não duplica nada.
    """
    for tentativa in range(1, TENTATIVAS_BANCO_OCUPADO + 1):
        try:
            lote = modelo.objects.get(pk=lote_id)
            lote.status = modelo.Status.PROCESSANDO
            lote.mensagem_erro = ""
            lote.save(update_fields=["status", "mensagem_erro"])
            etapas(lote)
            return
        except OperationalError as erro:
            if "locked" in str(erro) and tentativa < TENTATIVAS_BANCO_OCUPADO:
                logger.warning("Banco ocupado ao processar %s %s; tentando de novo", modelo._meta.verbose_name, lote_id)
                time.sleep(ESPERA_BANCO_OCUPADO_SEGUNDOS)
                continue
            falha = erro
        except Exception as erro:
            falha = erro
        logger.exception("Erro ao processar %s %s", modelo._meta.verbose_name, lote_id, exc_info=falha)
        modelo.objects.filter(pk=lote_id).update(
            status=modelo.Status.ERRO, mensagem_erro=f"Não foi possível processar o PDF: {falha}",
        )
        return


# ---------------------------------------------------------------------------
# Pendências (páginas que não foram entregues automaticamente)
# ---------------------------------------------------------------------------
def marcar_resolvida(pendencia, funcionario, usuario):
    """funcionario=None quando a página não é de ninguém (capa, resumo, página em branco)."""
    pendencia.resolvida = True
    pendencia.funcionario = funcionario
    pendencia.resolvida_em = timezone.now()
    pendencia.resolvida_por = usuario
    pendencia.save()
