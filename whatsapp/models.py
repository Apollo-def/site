from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


class Conversa(models.Model):
    """
    A conversa de um número de celular com o assistente. Uma por número (e outra separada para o
    simulador do painel). O funcionário é identificado pelo celular do cadastro.
    """

    class Estado(models.TextChoices):
        NOVA = "nova", "Nova"
        AGUARDANDO_PERFIL = "aguardando_perfil", "Número fora do cadastro: funcionário, vaga ou outro assunto?"
        AGUARDANDO_NOME = "aguardando_nome", "Número fora do cadastro: aguardando o nome"
        AGUARDANDO_MATRICULA = "aguardando_matricula", "Número fora do cadastro: aguardando a matrícula"
        AGUARDANDO_CPF = "aguardando_cpf", "Número fora do cadastro: aguardando o CPF"
        ATENDIMENTO = "atendimento", "Com o assistente"
        AGUARDANDO_MES = "aguardando_mes", "Assistente: aguardando o mês"
        AGUARDANDO_OPCAO_VT = "aguardando_opcao_vt", "Assistente: aguardando utilizar/cancelar VT"
        AGUARDANDO_TERMO = "aguardando_termo", "Assistente: aguardando o termo assinado"
        FARDAMENTO = "fardamento", "Assistente: pedido de fardamento (peças e tamanhos)"
        HUMANO = "humano", "Com atendente do RH"

    numero = models.CharField("número (WhatsApp)", max_length=20, db_index=True)
    simulada = models.BooleanField("conversa do simulador", default=False,
                                   help_text="Criada no simulador do painel; não usa o WhatsApp de verdade.")
    funcionario = models.ForeignKey("contas.Funcionario", verbose_name="funcionário", on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="conversas_whatsapp")
    estado = models.CharField("situação", max_length=25, choices=Estado.choices, default=Estado.NOVA)
    contexto = models.JSONField("dados do pedido em andamento", default=dict, blank=True)
    aguardando_humano_desde = models.DateTimeField("transferida para o RH em", null=True, blank=True)
    atendente = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="atendente", on_delete=models.SET_NULL,
                                  null=True, blank=True, related_name="+")
    ultima_entrada_em = models.DateTimeField("última mensagem do funcionário", null=True, blank=True)
    criada_em = models.DateTimeField("iniciada em", auto_now_add=True)
    atualizada_em = models.DateTimeField("atualizada em", auto_now=True)

    class Meta:
        verbose_name = "conversa do WhatsApp"
        verbose_name_plural = "atendimentos (conversas do WhatsApp)"
        ordering = ["-atualizada_em"]
        constraints = [models.UniqueConstraint(fields=["numero", "simulada"], name="uma_conversa_por_numero")]
        permissions = [("atender_whatsapp", "Atender conversas do WhatsApp")]

    def __str__(self):
        nome = self.funcionario.nome_completo if self.funcionario_id else "número não identificado"
        return f"{nome}{' (simulador)' if self.simulada else ''}"

    @property
    def dentro_da_janela(self):
        """A Meta só deixa mandar mensagem livre até 24 h depois da última mensagem do funcionário."""
        return bool(self.ultima_entrada_em and self.ultima_entrada_em >= timezone.now() - timedelta(hours=24))


class Mensagem(models.Model):
    class Direcao(models.TextChoices):
        ENTRADA = "entrada", "Recebida"
        SAIDA = "saida", "Enviada"

    class Autor(models.TextChoices):
        FUNCIONARIO = "funcionario", "Funcionário"
        ASSISTENTE = "assistente", "Assistente"
        RH = "rh", "RH"

    conversa = models.ForeignKey(Conversa, on_delete=models.CASCADE, related_name="mensagens")
    direcao = models.CharField("direção", max_length=10, choices=Direcao.choices)
    autor = models.CharField("autor", max_length=12, choices=Autor.choices)
    texto = models.TextField("texto", blank=True)
    anexo_nome = models.CharField("arquivo", max_length=150, blank=True)
    anexo_caminho = models.CharField("arquivo (armazenamento privado)", max_length=255, blank=True)
    id_externo = models.CharField("id no WhatsApp", max_length=128, blank=True, db_index=True)
    enviado_por = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="enviada por", on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")
    falhou = models.BooleanField("envio falhou", default=False)
    criada_em = models.DateTimeField("data/hora", auto_now_add=True)

    class Meta:
        verbose_name = "mensagem"
        verbose_name_plural = "mensagens"
        ordering = ["criada_em", "id"]

    def __str__(self):
        return f"{self.get_autor_display()}: {self.texto[:60]}"


class PerguntaSemResposta(models.Model):
    """O que o assistente não soube responder: vira sugestão de orientação para o RH escrever."""

    conversa = models.ForeignKey(Conversa, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    funcionario = models.ForeignKey("contas.Funcionario", verbose_name="funcionário", on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")
    texto = models.TextField("pergunta")
    resolvida = models.BooleanField("já virou orientação / respondida", default=False)
    criada_em = models.DateTimeField("perguntada em", auto_now_add=True)

    class Meta:
        verbose_name = "pergunta sem resposta"
        verbose_name_plural = "perguntas sem resposta"
        ordering = ["resolvida", "-criada_em"]

    def __str__(self):
        return self.texto[:80]


class PedidoCadastro(models.Model):
    """
    Número que ainda não está no cadastro: o assistente pede nome e matrícula e o RH confere.
    Nada é liberado sozinho: só o RH liga o número ao funcionário certo (senão qualquer pessoa que dissesse
    o nome de um colega receberia os documentos dele).
    """

    class Situacao(models.TextChoices):
        PENDENTE = "pendente", "Aguardando o RH"
        LIBERADO = "liberado", "Liberado"
        RECUSADO = "recusado", "Recusado"

    conversa = models.ForeignKey(Conversa, on_delete=models.CASCADE, related_name="pedidos_cadastro")
    numero = models.CharField("número (WhatsApp)", max_length=20, db_index=True)
    nome_informado = models.CharField("nome informado", max_length=150)
    matricula_informada = models.CharField("matrícula informada", max_length=30, blank=True)
    cpf_informado = models.CharField(
        "CPF informado", max_length=11, blank=True,
        help_text="O RH só consegue liberar para o funcionário que tem este CPF no cadastro.",
    )
    situacao = models.CharField("situação", max_length=10, choices=Situacao.choices, default=Situacao.PENDENTE)
    funcionario = models.ForeignKey("contas.Funcionario", verbose_name="funcionário", on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name="+")
    resolvido_por = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="resolvido por", on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name="+")
    resolvido_em = models.DateTimeField("resolvido em", null=True, blank=True)
    criado_em = models.DateTimeField("pedido em", auto_now_add=True)

    class Meta:
        verbose_name = "cadastro pendente (WhatsApp)"
        verbose_name_plural = "cadastros pendentes (WhatsApp)"
        ordering = ["situacao", "-criado_em"]

    def __str__(self):
        return f"{self.nome_informado} ({self.get_situacao_display()})"

    @property
    def cpf_mascarado(self):
        """529.***.***-25 (LGPD: o painel não mostra o CPF inteiro)."""
        return mascarar_cpf(self.cpf_informado) if self.cpf_informado else ""


def mascarar_cpf(cpf):
    return f"{cpf[:3]}.***.***-{cpf[-2:]}" if len(cpf) == 11 else cpf
