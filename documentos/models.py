from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils import timezone

from portal.armazenamento import CaminhoUnico, apagar_arquivos_ao_excluir


class TipoDocumento(models.TextChoices):
    TERMO_VT = "termo_vt", "Termo de opção de vale-transporte"
    REGISTRO_EMPREGADO = "registro_empregado", "Registro de empregado"
    INFORME_RENDIMENTOS = "informe_rendimentos", "Informe de rendimentos"
    AVISO_FERIAS = "aviso_ferias", "Aviso / recibo de férias"
    DECLARACAO = "declaracao", "Declaração"
    OUTRO = "outro", "Outro"


# Documentos que chegam num PDF único com todos os funcionários (um por página) e são separados pelo CPF.
TIPOS_EM_LOTE = [
    (TipoDocumento.TERMO_VT.value, TipoDocumento.TERMO_VT.label),
    (TipoDocumento.REGISTRO_EMPREGADO.value, TipoDocumento.REGISTRO_EMPREGADO.label),
]


class DocumentoFuncionario(models.Model):
    """Documento enviado pelo RH para UM funcionário (informe de rendimentos, aviso de férias...)."""

    Tipo = TipoDocumento

    funcionario = models.ForeignKey(
        "contas.Funcionario", verbose_name="funcionário", on_delete=models.PROTECT, related_name="documentos"
    )
    tipo = models.CharField("tipo", max_length=30, choices=TipoDocumento.choices)
    titulo = models.CharField("título", max_length=150, help_text="Ex.: Informe de Rendimentos 2025")
    descricao = models.TextField("observação para o funcionário", blank=True)
    arquivo = models.FileField(
        "arquivo", upload_to=CaminhoUnico("documentos"),
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])],
    )
    lote = models.ForeignKey(
        "LoteDocumento", verbose_name="envio de origem", on_delete=models.SET_NULL, null=True, blank=True,
        editable=False, related_name="documentos",
    )
    paginas = models.CharField("páginas no PDF original", max_length=50, blank=True, editable=False)
    vinculado_manualmente = models.BooleanField("vinculado manualmente pelo RH", default=False, editable=False)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="enviado por", on_delete=models.SET_NULL, null=True, blank=True,
        editable=False, related_name="+",
    )
    enviado_em = models.DateTimeField("enviado em", auto_now_add=True)

    class Meta:
        verbose_name = "documento individual"
        verbose_name_plural = "documentos individuais"
        ordering = ["-enviado_em"]

    def __str__(self):
        return f"{self.titulo} — {self.funcionario}"


class LoteDocumento(models.Model):
    """Um upload do RH: PDF único com o mesmo documento de todos os funcionários (ex.: termo de VT)."""

    class Status(models.TextChoices):
        AGUARDANDO = "aguardando", "Aguardando processamento"
        PROCESSANDO = "processando", "Processando"
        CONCLUIDO = "concluido", "Concluído"
        ERRO = "erro", "Erro"

    tipo = models.CharField("documento", max_length=30, choices=TIPOS_EM_LOTE)
    titulo = models.CharField("título que o funcionário vê", max_length=150)
    arquivo = models.FileField(
        "PDF com os documentos de todos", upload_to=CaminhoUnico("documentos/lotes"),
        validators=[FileExtensionValidator(["pdf"])],
    )
    status = models.CharField("situação", max_length=15, choices=Status.choices, default=Status.AGUARDANDO)
    total_paginas = models.PositiveIntegerField("total de páginas", default=0)
    paginas_processadas = models.PositiveIntegerField("páginas processadas", default=0)
    total_distribuidos = models.PositiveIntegerField("documentos entregues", default=0)
    total_nao_encontrados = models.PositiveIntegerField("CPFs não cadastrados", default=0)
    total_sem_cpf = models.PositiveIntegerField("páginas sem CPF", default=0)
    mensagem_erro = models.TextField("mensagem de erro", blank=True)
    avisos = models.TextField("avisos para conferência", blank=True)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="enviado por", on_delete=models.SET_NULL, null=True, blank=True,
        editable=False,
    )
    enviado_em = models.DateTimeField("enviado em", auto_now_add=True)
    concluido_em = models.DateTimeField("concluído em", null=True, blank=True)

    class Meta:
        verbose_name = "envio de documento para todos"
        verbose_name_plural = "enviar PDF de documentos (termo de VT, registro…)"
        ordering = ["-enviado_em"]

    def __str__(self):
        return f"{self.titulo} ({timezone.localtime(self.enviado_em):%d/%m/%Y})" if self.enviado_em else self.titulo

    @property
    def progresso(self):
        if not self.total_paginas:
            return 0
        return round(100 * self.paginas_processadas / self.total_paginas)


class PendenciaDocumento(models.Model):
    """Páginas do PDF que o sistema não conseguiu entregar a ninguém automaticamente."""

    class Motivo(models.TextChoices):
        SEM_CPF = "sem_cpf", "CPF não encontrado na página"
        NAO_CADASTRADO = "nao_cadastrado", "CPF não cadastrado no sistema"
        NOME_DIVERGENTE = "nome_divergente", "Nome no PDF diferente do cadastro"
        VARIOS_CPFS = "varios_cpfs", "Mais de um CPF na mesma página"
        SEM_TEXTO = "sem_texto", "Página sem texto (imagem escaneada)"

    lote = models.ForeignKey(LoteDocumento, verbose_name="envio", on_delete=models.CASCADE, related_name="pendencias")
    paginas = models.CharField("páginas", max_length=50)
    cpf_lido = models.CharField("CPF lido no PDF", max_length=14, blank=True)
    nome_lido = models.CharField("nome lido no PDF", max_length=200, blank=True)
    motivo = models.CharField("motivo", max_length=20, choices=Motivo.choices)
    arquivo = models.FileField("PDF das páginas", upload_to=CaminhoUnico("documentos/pendentes"))
    resolvida = models.BooleanField("resolvida", default=False)
    funcionario = models.ForeignKey(
        "contas.Funcionario", verbose_name="vinculado a", on_delete=models.SET_NULL, null=True, blank=True,
    )
    resolvida_em = models.DateTimeField("resolvida em", null=True, blank=True)
    resolvida_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="resolvida por", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "pendência de documento"
        verbose_name_plural = "pendências de documentos"
        ordering = ["lote", "id"]

    def __str__(self):
        return f"{self.lote} — pág. {self.paginas}"


# Ao apagar um registro, o arquivo dele também é apagado do armazenamento.
apagar_arquivos_ao_excluir(LoteDocumento, DocumentoFuncionario, PendenciaDocumento)
