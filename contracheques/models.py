from django.conf import settings
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models

from portal.armazenamento import CaminhoUnico, apagar_arquivos_ao_excluir

MESES = [
    (1, "Janeiro"), (2, "Fevereiro"), (3, "Março"), (4, "Abril"), (5, "Maio"), (6, "Junho"),
    (7, "Julho"), (8, "Agosto"), (9, "Setembro"), (10, "Outubro"), (11, "Novembro"), (12, "Dezembro"),
]


class TipoFolha(models.TextChoices):
    MENSAL = "mensal", "Folha mensal"
    DECIMO_TERCEIRO = "13", "13º salário"
    FERIAS = "ferias", "Férias"
    ADIANTAMENTO = "adiantamento", "Adiantamento"
    COMPLEMENTAR = "complementar", "Folha complementar"


class Competencia(models.Model):
    """Campos comuns: mês/ano de referência e tipo de folha."""

    ano = models.PositiveSmallIntegerField("ano", validators=[MinValueValidator(2000), MaxValueValidator(2100)])
    mes = models.PositiveSmallIntegerField("mês", choices=MESES)
    tipo = models.CharField("tipo de folha", max_length=15, choices=TipoFolha.choices, default=TipoFolha.MENSAL)

    class Meta:
        abstract = True

    @property
    def competencia(self):
        return f"{self.mes:02d}/{self.ano}"

    @property
    def competencia_por_extenso(self):
        return f"{dict(MESES)[self.mes]} de {self.ano}"


class LoteContracheque(Competencia):
    """Um upload mensal: o PDF único com os contracheques de todos os funcionários."""

    class Status(models.TextChoices):
        AGUARDANDO = "aguardando", "Aguardando processamento"
        PROCESSANDO = "processando", "Processando"
        CONCLUIDO = "concluido", "Concluído"
        ERRO = "erro", "Erro"
        SUBSTITUIDO = "substituido", "Substituído por um envio mais novo"

    contrato = models.ForeignKey(
        "contas.Contrato", verbose_name="contrato", on_delete=models.PROTECT, null=True, blank=True,
        related_name="lotes_contracheque", help_text="Opcional. Deixe em branco se o PDF tiver vários contratos.",
    )
    arquivo = models.FileField(
        "PDF com todos os contracheques", upload_to=CaminhoUnico("contracheques/lotes"),
        validators=[FileExtensionValidator(["pdf"])],
    )
    status = models.CharField("situação", max_length=15, choices=Status.choices, default=Status.AGUARDANDO)
    total_paginas = models.PositiveIntegerField("total de páginas", default=0)
    paginas_processadas = models.PositiveIntegerField("páginas processadas", default=0)
    total_distribuidos = models.PositiveIntegerField("contracheques distribuídos", default=0)
    total_nao_encontrados = models.PositiveIntegerField("matrículas não cadastradas", default=0)
    total_sem_matricula = models.PositiveIntegerField("páginas sem matrícula", default=0)
    mensagem_erro = models.TextField("mensagem de erro", blank=True)
    avisos = models.TextField("avisos para conferência", blank=True)
    competencia_detectada = models.CharField("competência escrita no PDF", max_length=40, blank=True)
    substituir_existentes = models.BooleanField("substituir envio anterior da mesma competência", default=False)
    enviado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="enviado por", on_delete=models.SET_NULL, null=True, blank=True,
        editable=False,
    )
    enviado_em = models.DateTimeField("enviado em", auto_now_add=True)
    concluido_em = models.DateTimeField("concluído em", null=True, blank=True)

    class Meta:
        verbose_name = "envio do PDF do mês"
        verbose_name_plural = "enviar PDF do mês (todos os funcionários)"
        ordering = ["-ano", "-mes", "-enviado_em"]

    def __str__(self):
        contrato = f" — {self.contrato}" if self.contrato_id else ""
        return f"{self.competencia} ({self.get_tipo_display()}){contrato}"

    @property
    def progresso(self):
        if not self.total_paginas:
            return 0
        return round(100 * self.paginas_processadas / self.total_paginas)


class Contracheque(Competencia):
    """Contracheque individual de um funcionário em uma competência."""

    funcionario = models.ForeignKey(
        "contas.Funcionario", verbose_name="funcionário", on_delete=models.PROTECT, related_name="contracheques"
    )
    lote = models.ForeignKey(
        LoteContracheque, verbose_name="upload de origem", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="contracheques",
    )
    arquivo = models.FileField(
        "arquivo PDF", upload_to=CaminhoUnico("contracheques/individuais"),
        validators=[FileExtensionValidator(["pdf"])],
    )
    paginas = models.CharField("páginas no PDF original", max_length=50, blank=True)
    vinculado_manualmente = models.BooleanField("vinculado manualmente pelo RH", default=False)
    criado_em = models.DateTimeField("disponibilizado em", auto_now_add=True)

    class Meta:
        verbose_name = "contracheque"
        verbose_name_plural = "contracheques entregues (consulta)"
        ordering = ["-ano", "-mes", "funcionario__nome_completo"]
        constraints = [
            models.UniqueConstraint(fields=["funcionario", "ano", "mes", "tipo"], name="contracheque_unico_por_competencia")
        ]

    def __str__(self):
        return f"{self.funcionario} — {self.competencia} ({self.get_tipo_display()})"


class PaginaNaoIdentificada(models.Model):
    """Páginas do PDF mensal que o sistema não conseguiu entregar a ninguém automaticamente."""

    class Motivo(models.TextChoices):
        SEM_MATRICULA = "sem_matricula", "Matrícula não encontrada na página"
        NAO_CADASTRADA = "nao_cadastrada", "Matrícula não cadastrada no sistema"
        NOME_DIVERGENTE = "nome_divergente", "Nome no PDF diferente do cadastro"
        VARIAS_MATRICULAS = "varias_matriculas", "Mais de uma matrícula na mesma página"
        SEM_TEXTO = "sem_texto", "Página sem texto (imagem escaneada)"

    lote = models.ForeignKey(LoteContracheque, verbose_name="upload", on_delete=models.CASCADE,
                             related_name="paginas_nao_identificadas")
    paginas = models.CharField("páginas", max_length=50)
    matricula_lida = models.CharField("matrícula lida no PDF", max_length=30, blank=True)
    nome_lido = models.CharField("nome lido no PDF", max_length=200, blank=True)
    motivo = models.CharField("motivo", max_length=20, choices=Motivo.choices)
    arquivo = models.FileField("PDF das páginas", upload_to=CaminhoUnico("contracheques/pendentes"))
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
        verbose_name = "página não identificada"
        verbose_name_plural = "pendências (páginas não identificadas)"
        ordering = ["lote", "id"]

    def __str__(self):
        return f"{self.lote} — pág. {self.paginas}"


# Ao apagar um registro, o arquivo dele também é apagado do armazenamento.
apagar_arquivos_ao_excluir(LoteContracheque, Contracheque, PaginaNaoIdentificada)
