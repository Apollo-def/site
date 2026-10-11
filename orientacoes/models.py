from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from portal.armazenamento import CaminhoUnico, apagar_arquivos_ao_excluir


class Categoria(models.Model):
    """Ex.: Vale-Transporte, Vale-Alimentação, Férias, Documentos Pessoais, Outros."""

    nome = models.CharField("nome", max_length=80, unique=True)
    descricao = models.CharField("descrição", max_length=200, blank=True)
    ordem = models.PositiveSmallIntegerField("ordem", default=0)
    ativa = models.BooleanField("ativa", default=True)

    class Meta:
        verbose_name = "categoria"
        verbose_name_plural = "categorias"
        ordering = ["ordem", "nome"]

    def __str__(self):
        return self.nome


class OrientacaoQuerySet(models.QuerySet):
    def publicadas_para(self, funcionario):
        """Somente o que está publicado para o contrato do funcionário."""
        return (
            self.filter(status=Orientacao.Status.PUBLICADO, categoria__ativa=True)
            .filter(Q(para_todos=True) | Q(contratos=funcionario.contrato_id))
            .distinct()
        )


class Orientacao(models.Model):
    """Orientação (passo a passo) ou formulário geral, visível para vários funcionários."""

    class Tipo(models.TextChoices):
        ORIENTACAO = "orientacao", "Orientação (passo a passo)"
        FORMULARIO = "formulario", "Formulário / termo para download"

    class Status(models.TextChoices):
        RASCUNHO = "rascunho", "Rascunho"
        PUBLICADO = "publicado", "Publicado"
        ARQUIVADO = "arquivado", "Arquivado"

    tipo = models.CharField("tipo", max_length=15, choices=Tipo.choices, default=Tipo.ORIENTACAO)
    titulo = models.CharField("título", max_length=200)
    categoria = models.ForeignKey(Categoria, verbose_name="categoria", on_delete=models.PROTECT,
                                  related_name="orientacoes")
    resumo = models.CharField("resumo", max_length=300, blank=True, help_text="Aparece na lista, abaixo do título.")
    conteudo = models.TextField("texto", blank=True,
                                help_text="Texto com negrito, listas e passo a passo (editor chega na Etapa 6).")
    status = models.CharField("status", max_length=15, choices=Status.choices, default=Status.RASCUNHO,
                              db_index=True)

    para_todos = models.BooleanField("publicar para todos os contratos", default=True)
    contratos = models.ManyToManyField(
        "contas.Contrato", verbose_name="somente para os contratos", blank=True, related_name="orientacoes"
    )
    tipo_solicitacao = models.ForeignKey(
        "solicitacoes.TipoSolicitacao", verbose_name="botão para a solicitação", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="orientacoes",
        help_text="Opcional: mostra um botão que leva direto a esta solicitação.",
    )
    ordem = models.PositiveSmallIntegerField("ordem", default=0)

    criado_por = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="criado por", on_delete=models.SET_NULL,
                                   null=True, blank=True, editable=False, related_name="+")
    criado_em = models.DateTimeField("criado em", auto_now_add=True)
    publicado_em = models.DateTimeField("publicado em", null=True, blank=True, editable=False)
    atualizado_em = models.DateTimeField("atualizado em", default=timezone.now, editable=False,
                                         help_text="Muda quando o texto ou os anexos são alterados.")

    objects = OrientacaoQuerySet.as_manager()

    class Meta:
        verbose_name = "orientação / formulário"
        verbose_name_plural = "orientações e formulários"
        ordering = ["categoria__ordem", "ordem", "titulo"]

    def __str__(self):
        return self.titulo

    def save(self, *args, **kwargs):
        if self.status == self.Status.PUBLICADO and self.publicado_em is None:
            self.publicado_em = timezone.now()
        super().save(*args, **kwargs)

    def marcar_como_atualizada(self):
        self.atualizado_em = timezone.now()
        type(self).objects.filter(pk=self.pk).update(atualizado_em=self.atualizado_em)


class Anexo(models.Model):
    """
    Arquivo (termo em PDF) de uma orientação. Para trocar por uma versão nova, basta enviar
    o novo arquivo no mesmo anexo: a versão anterior vai automaticamente para o histórico
    (e deixa de aparecer para o funcionário).
    """

    orientacao = models.ForeignKey(Orientacao, verbose_name="orientação", on_delete=models.CASCADE,
                                   related_name="anexos")
    titulo = models.CharField("nome do arquivo para o funcionário", max_length=150,
                              help_text="Ex.: Termo de Cancelamento de Vale-Transporte")
    arquivo = models.FileField(
        "arquivo", upload_to=CaminhoUnico("orientacoes"),
        validators=[FileExtensionValidator(["pdf", "doc", "docx", "jpg", "jpeg", "png"])],
    )
    versao = models.PositiveIntegerField("versão", default=1, editable=False)
    ordem = models.PositiveSmallIntegerField("ordem", default=0)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "anexo"
        verbose_name_plural = "anexos"
        ordering = ["ordem", "id"]

    def __str__(self):
        return f"{self.titulo} (v{self.versao})"

    def save(self, *args, **kwargs):
        anterior = None
        if self.pk:
            anterior = type(self).objects.filter(pk=self.pk).first()
        arquivo_trocado = anterior is not None and anterior.arquivo.name != self.arquivo.name
        if arquivo_trocado:
            self.versao = anterior.versao + 1
        super().save(*args, **kwargs)
        if arquivo_trocado:
            VersaoAnteriorAnexo.objects.create(anexo=self, versao=anterior.versao, arquivo=anterior.arquivo.name,
                                               enviado_em=anterior.atualizado_em)
        if anterior is None or arquivo_trocado:
            self.orientacao.marcar_como_atualizada()


class VersaoAnteriorAnexo(models.Model):
    """Histórico de versões antigas de um anexo. Visível só para o RH."""

    anexo = models.ForeignKey(Anexo, on_delete=models.CASCADE, related_name="versoes_anteriores")
    versao = models.PositiveIntegerField("versão")
    arquivo = models.FileField("arquivo", upload_to=CaminhoUnico("orientacoes/historico"))
    enviado_em = models.DateTimeField("enviado em", null=True, blank=True)
    substituido_em = models.DateTimeField("substituído em", auto_now_add=True)

    class Meta:
        verbose_name = "versão anterior"
        verbose_name_plural = "versões anteriores"
        ordering = ["-versao"]

    def __str__(self):
        return f"{self.anexo.titulo} (v{self.versao})"


# Ao apagar um registro, o arquivo dele também é apagado do armazenamento.
apagar_arquivos_ao_excluir(Anexo, VersaoAnteriorAnexo)
