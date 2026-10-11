from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from portal.armazenamento import CaminhoUnico, apagar_arquivos_ao_excluir

class TipoSolicitacao(models.Model):
    """Tipo de pedido (ex.: Cancelamento de VT). Os de VT são usados pelo assistente do WhatsApp."""

    # Tipos usados pelo assistente (termo de VT devolvido pelo WhatsApp). Os criados pelo RH ficam sem código.
    VT_UTILIZAR = "vt_utilizar"
    VT_CANCELAR = "vt_cancelar"
    FARDAMENTO = "fardamento"

    codigo = models.SlugField("código interno", max_length=40, unique=True, null=True, blank=True, editable=False)
    nome = models.CharField("nome", max_length=120, unique=True)
    descricao = models.TextField("explicação curta para o funcionário", blank=True)
    ativo = models.BooleanField("disponível para os funcionários", default=True)
    ordem = models.PositiveSmallIntegerField("ordem no menu", default=0)

    para_todos = models.BooleanField("disponível para todos os contratos", default=True)
    contratos = models.ManyToManyField(
        "contas.Contrato", verbose_name="somente para os contratos", blank=True, related_name="tipos_solicitacao",
        help_text="Usado quando NÃO estiver marcado 'disponível para todos os contratos'.",
    )

    class Meta:
        verbose_name = "tipo de solicitação"
        verbose_name_plural = "tipos de solicitação"
        ordering = ["ordem", "nome"]

    def __str__(self):
        return self.nome


class SolicitacaoQuerySet(models.QuerySet):
    def pendentes(self):
        return self.filter(status__in=[Solicitacao.Status.PENDENTE, Solicitacao.Status.EM_ANALISE])


class Solicitacao(models.Model):
    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        EM_ANALISE = "em_analise", "Em análise"
        CONCLUIDA = "concluida", "Concluída"
        RECUSADA = "recusada", "Recusada"

    protocolo = models.CharField("protocolo", max_length=20, unique=True, null=True, blank=True, editable=False)
    funcionario = models.ForeignKey(
        "contas.Funcionario", verbose_name="funcionário", on_delete=models.PROTECT, related_name="solicitacoes"
    )
    tipo = models.ForeignKey(TipoSolicitacao, verbose_name="tipo", on_delete=models.PROTECT,
                             related_name="solicitacoes")
    status = models.CharField("status", max_length=15, choices=Status.choices, default=Status.PENDENTE,
                              db_index=True)
    observacao_funcionario = models.TextField("observação do funcionário", blank=True)

    class Origem(models.TextChoices):
        WHATSAPP = "whatsapp", "WhatsApp"
        PAINEL = "painel", "Painel do RH"

    origem = models.CharField("chegou por", max_length=10, choices=Origem.choices, default=Origem.WHATSAPP)
    termo_assinado = models.FileField(
        "termo assinado enviado", upload_to=CaminhoUnico("termos/assinados"), blank=True,
        validators=[FileExtensionValidator(["pdf", "jpg", "jpeg", "png"])],
    )

    # Resposta do RH
    motivo_recusa = models.TextField("motivo da recusa", blank=True)
    resposta_rh = models.TextField("mensagem do RH para o funcionário", blank=True)
    data_prevista_vigencia = models.DateField(
        "data prevista para entrar em vigor", null=True, blank=True, help_text="Ex.: data da próxima folha."
    )
    atendido_por = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="atendido por", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    criado_em = models.DateTimeField("aberta em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizada em", auto_now=True)
    finalizada_em = models.DateTimeField("finalizada em", null=True, blank=True)

    objects = SolicitacaoQuerySet.as_manager()

    class Meta:
        verbose_name = "solicitação"
        verbose_name_plural = "solicitações"
        ordering = ["-criado_em"]
        constraints = [
            models.CheckConstraint(
                condition=~Q(status="recusada") | ~Q(motivo_recusa=""), name="recusa_exige_motivo"
            )
        ]

    def __str__(self):
        return f"{self.protocolo or 'Nova'} — {self.tipo} — {self.funcionario}"

    def clean(self):
        if self.status == self.Status.RECUSADA and not self.motivo_recusa.strip():
            raise ValidationError({"motivo_recusa": "Informe o motivo da recusa. O funcionário verá este texto."})

    def save(self, *args, **kwargs):
        if self.status in (self.Status.CONCLUIDA, self.Status.RECUSADA) and not self.finalizada_em:
            self.finalizada_em = timezone.now()
        elif self.status in (self.Status.PENDENTE, self.Status.EM_ANALISE):
            self.finalizada_em = None
        super().save(*args, **kwargs)
        if not self.protocolo:
            # Protocolo legível: ANO-NÚMERO (ex.: 2026-000042)
            self.protocolo = f"{timezone.localtime(self.criado_em):%Y}-{self.pk:06d}"
            type(self).objects.filter(pk=self.pk).update(protocolo=self.protocolo)


class HistoricoSolicitacao(models.Model):
    """Cada mudança de status fica registrada para o funcionário e o RH acompanharem."""

    solicitacao = models.ForeignKey(Solicitacao, on_delete=models.CASCADE, related_name="historico")
    status = models.CharField("status", max_length=15, choices=Solicitacao.Status.choices)
    observacao = models.TextField("observação", blank=True)
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="por", on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="+")
    data_hora = models.DateTimeField("data/hora", auto_now_add=True)

    class Meta:
        verbose_name = "histórico"
        verbose_name_plural = "histórico da solicitação"
        ordering = ["data_hora"]

    def __str__(self):
        return f"{self.get_status_display()} em {self.data_hora:%d/%m/%Y %H:%M}"


class ItemFardamento(models.Model):
    """Uma peça de um pedido de fardamento (camisa M, bota 41...). Uma linha por peça: é o que o relatório lista."""

    class Peca(models.TextChoices):
        CAMISA = "camisa", "Camisa"
        CALCA = "calca", "Calça"
        BOTA = "bota", "Bota / sapato"
        OUTRA = "outra", "Outra peça"

    solicitacao = models.ForeignKey(Solicitacao, verbose_name="pedido", on_delete=models.CASCADE,
                                    related_name="itens_fardamento")
    peca = models.CharField("peça", max_length=10, choices=Peca.choices)
    descricao = models.CharField("qual peça", max_length=60, blank=True, help_text="Quando for \"outra peça\".")
    tamanho = models.CharField("tamanho", max_length=10)

    class Meta:
        verbose_name = "peça de fardamento pedida"
        verbose_name_plural = "fardamento: peças pedidas (relatório)"
        ordering = ["-solicitacao__criado_em", "id"]

    def __str__(self):
        return f"{self.nome_da_peca} {self.tamanho}"

    @property
    def nome_da_peca(self):
        return self.descricao if self.peca == self.Peca.OUTRA and self.descricao else self.get_peca_display()


# Ao apagar um registro, o arquivo dele também é apagado do armazenamento.
apagar_arquivos_ao_excluir(Solicitacao)
