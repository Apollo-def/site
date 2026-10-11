from django.conf import settings
from django.db import models

from portal.armazenamento import CaminhoUnico, apagar_arquivos_ao_excluir


class Ocorrencia(models.Model):
    """
    Falta, atestado, cobertura ou extra mandado pelos supervisores no grupo do WhatsApp
    ("Faltas, Atestados e Coberturas - SEMED"). O sistema lê a mensagem, separa os campos e procura o funcionário;
    o que não der para ter certeza fica para o RH conferir.
    """

    class Tipo(models.TextChoices):
        FALTA = "falta", "Falta"
        ATESTADO = "atestado", "Atestado"
        COBERTURA = "cobertura", "Cobertura"
        EXTRA = "extra", "Extra"

    class Situacao(models.TextChoices):
        CONFERIR = "conferir", "Conferir (RH)"
        OK = "ok", "Conferida"

    tipo = models.CharField("tipo", max_length=10, choices=Tipo.choices, blank=True)
    data = models.DateField("data", db_index=True)
    funcionario = models.ForeignKey("contas.Funcionario", verbose_name="funcionário", on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="ocorrencias")
    nome_informado = models.CharField("nome escrito no grupo", max_length=150, blank=True)
    escola_informada = models.CharField("escola/posto escrito no grupo", max_length=200, blank=True)
    coberto = models.ForeignKey("contas.Funcionario", verbose_name="cobrindo (quem faltou)", on_delete=models.SET_NULL,
                                null=True, blank=True, related_name="+")
    coberto_nome_informado = models.CharField("cobrindo (escrito no grupo)", max_length=150, blank=True)
    atestado_inicio = models.DateField("atestado de", null=True, blank=True)
    atestado_fim = models.DateField("atestado até", null=True, blank=True)
    observacao = models.TextField("observação", blank=True)
    anexo = models.FileField("foto/arquivo (ex.: atestado)", upload_to=CaminhoUnico("ocorrencias"), blank=True)

    situacao = models.CharField("situação", max_length=10, choices=Situacao.choices, default=Situacao.CONFERIR,
                                db_index=True)
    motivo_conferir = models.CharField("o que conferir", max_length=300, blank=True)
    supervisor_nome = models.CharField("enviada por", max_length=100, blank=True)
    supervisor_numero = models.CharField("número de quem enviou", max_length=20, blank=True)
    texto_original = models.TextField("mensagem original", blank=True)
    id_externo = models.CharField("id no WhatsApp", max_length=128, unique=True)
    recebida_em = models.DateTimeField("recebida em", auto_now_add=True)
    conferida_por = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="conferida por", on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name="+")
    conferida_em = models.DateTimeField("conferida em", null=True, blank=True)
    # 👍 que o ADM (o celular do chip) deu na mensagem do grupo: "visto / ok".
    ok_adm_em = models.DateTimeField("OK do ADM no grupo (👍) em", null=True, blank=True)
    ok_adm_emoji = models.CharField("reação do ADM", max_length=10, blank=True)

    class Meta:
        verbose_name = "ocorrência (falta, atestado, cobertura, extra)"
        verbose_name_plural = "faltas, atestados, coberturas e extras"
        ordering = ["-data", "-recebida_em"]

    def __str__(self):
        nome = self.funcionario.nome_completo if self.funcionario_id else self.nome_informado
        return f"{self.get_tipo_display() or 'Ocorrência'} — {nome} — {self.data:%d/%m/%Y}"


apagar_arquivos_ao_excluir(Ocorrencia)
