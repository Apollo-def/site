from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.cache import cache
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models

from portal.armazenamento import CaminhoUnico

from .campos import CPFField
from .validadores import formatar_cpf, normalizar_celular, somente_digitos

AVISO_PRIVACIDADE_PADRAO = (
    "Para sua segurança, só envio documentos para o celular cadastrado no RH. As conversas ficam registradas "
    "conforme a LGPD."
)


class ConfiguracaoPortal(models.Model):
    """Configurações gerais que o RH pode mudar pelo painel, sem programador. Existe um registro só."""

    CHAVE_CACHE = "configuracao_portal"

    maximo_tentativas = models.PositiveSmallIntegerField(
        "senhas erradas antes de travar o login do RH", default=5,
        validators=[MinValueValidator(3), MaxValueValidator(20)],
        help_text="Depois disso o login do painel trava por 15 minutos.",
    )
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    # Assistente do Colaborador via WhatsApp
    class FormaEnvio(models.TextChoices):
        PDF = "pdf", "Arquivo PDF na conversa"
        LINK = "link", "Link seguro que vale por 24 horas"

    whatsapp_ativo = models.BooleanField(
        "atendimento pelo WhatsApp ligado", default=False,
        help_text="Desligado, o assistente responde que o atendimento está indisponível.",
    )
    whatsapp_numero_publico = models.CharField(
        "número do assistente (divulgado)", max_length=20, blank=True,
        help_text="Aparece na página inicial do portal com o botão \"Conversar no WhatsApp\". Ex.: (98) 98888-7777",
    )
    whatsapp_boas_vindas = models.TextField(
        "mensagem de boas-vindas", default="Olá, {nome}! Sou o Assistente do Colaborador do Grupo Global.",
        help_text="{nome} vira o primeiro nome do funcionário.",
    )
    whatsapp_aviso_privacidade = models.TextField(
        "aviso de privacidade", blank=True,
        default=AVISO_PRIVACIDADE_PADRAO,
        help_text="Mostrado no primeiro contato, junto com a mensagem de boas-vindas.",
    )
    whatsapp_horario_atendimento = models.CharField(
        "horário do atendimento humano", max_length=120, default="segunda a sexta, das 8h às 17h",
    )
    termo_vt_padrao = models.FileField(
        "termo de VT padrão (para todos)", upload_to=CaminhoUnico("modelos"), blank=True,
        validators=[FileExtensionValidator(["pdf"])],
        help_text="Formulário em branco enviado pelo assistente a quem não tem um termo individual (já preenchido) "
                  "vindo do envio em lote. O funcionário preenche, assina e devolve pelo WhatsApp.",
    )
    whatsapp_opcao_duvidas = models.BooleanField(
        "mostrar a opção \"Tirar uma dúvida\" no menu", default=False,
        help_text="Ligue quando a Central de orientações tiver as respostas (VT, VA, férias, documentos...). "
                  "Desligada, o menu não mostra a opção e \"Falar com uma pessoa do RH\" vira o número 5.",
    )
    whatsapp_texto_curriculo = models.TextField(
        "resposta para quem procura vaga / quer mandar currículo", blank=True,
        help_text="Ex.: \"Que bom que você quer trabalhar com a gente! Envie o seu currículo para rh@empresa.com.br "
                  "com a função no assunto (ex.: Porteiro).\" Em branco, o assistente diz só que este número não "
                  "recebe currículos.",
    )
    whatsapp_guardar_dias = models.PositiveSmallIntegerField(
        "guardar as conversas do WhatsApp por (dias)", default=365, validators=[MaxValueValidator(3650)],
        help_text="LGPD: mensagens mais antigas que isso são apagadas sozinhas toda noite (os pedidos, termos "
                  "assinados e a auditoria continuam). 0 = não apagar.",
    )
    whatsapp_forma_envio = models.CharField(
        "como enviar os documentos", max_length=10, choices=FormaEnvio.choices, default=FormaEnvio.PDF,
        help_text="O link é mais seguro (o arquivo não fica salvo no WhatsApp) e também fica na auditoria.",
    )

    class Meta:
        verbose_name = "configuração do portal"
        verbose_name_plural = "configuração do portal"

    def __str__(self):
        return "Configuração do portal"

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)
        cache.delete(self.CHAVE_CACHE)

    @property
    def link_whatsapp(self):
        """https://wa.me/55... do número divulgado ('' se não estiver preenchido)."""
        numero = normalizar_celular(self.whatsapp_numero_publico)
        return f"https://wa.me/{numero}" if numero else ""

    @classmethod
    def atual(cls):
        config = cache.get(cls.CHAVE_CACHE)
        if config is None:
            config, _ = cls.objects.get_or_create(pk=1)
            cache.set(cls.CHAVE_CACHE, config, 60)
        return config


class Contrato(models.Model):
    """Contrato / lotação onde o funcionário trabalha (ex.: SEMED, SEMCAS, TRT-16, TJMA)."""

    sigla = models.CharField("sigla", max_length=30, unique=True, help_text="Ex.: SEMED, TRT-16")
    nome = models.CharField("nome do órgão", max_length=200, blank=True)
    ativo = models.BooleanField("ativo", default=True)

    class Meta:
        verbose_name = "contrato / lotação"
        verbose_name_plural = "contratos / lotações"
        ordering = ["sigla"]

    def __str__(self):
        return self.sigla


class Usuario(AbstractUser):
    """
    Login do painel do RH. Os funcionários não têm login (são atendidos pelo WhatsApp); os logins antigos
    deles ficam desativados, só para o histórico da auditoria continuar ligado a eles.
    """

    tentativas_falhas = models.PositiveSmallIntegerField("tentativas de login erradas", default=0)
    bloqueado = models.BooleanField(
        "bloqueado", default=False,
        help_text="Travado por excesso de senhas erradas (limite em Configuração do portal). "
                  "Libera sozinho depois de 15 minutos, ou um administrador desbloqueia.",
    )
    bloqueado_em = models.DateTimeField("bloqueado em", null=True, blank=True)
    # Verificação em duas etapas (contas/duas_etapas.py): o segredo nunca aparece no painel.
    duas_etapas_ativa = models.BooleanField("verificação em duas etapas ativa", default=False)
    duas_etapas_segredo = models.CharField(max_length=64, blank=True, editable=False)
    duas_etapas_ultimo_passo = models.BigIntegerField(default=0, editable=False)
    duas_etapas_reserva = models.JSONField(default=list, blank=True, editable=False)

    class Meta:
        verbose_name = "usuário (login)"
        verbose_name_plural = "usuários (logins)"


class Funcionario(models.Model):
    class Situacao(models.TextChoices):
        ATIVO = "ativo", "Ativo"
        INATIVO = "inativo", "Inativo (desligado)"

    usuario = models.OneToOneField(  # login do tempo do site do funcionário (só histórico)
        settings.AUTH_USER_MODEL, verbose_name="login antigo", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="funcionario", editable=False,
    )
    nome_completo = models.CharField("nome completo", max_length=200)
    matricula = models.CharField("matrícula", max_length=30, unique=True)
    cpf = CPFField("CPF", unique=True, help_text="Pode digitar com ou sem pontuação.")
    data_nascimento = models.DateField("data de nascimento")
    contrato = models.ForeignKey(
        Contrato, verbose_name="contrato / lotação", on_delete=models.PROTECT, related_name="funcionarios"
    )
    cargo = models.CharField("cargo / tipo de posto", max_length=120, blank=True)
    instalacao = models.CharField("instalação", max_length=200, blank=True,
                                  help_text="Onde trabalha (ex.: escola, unidade, posto).")
    data_admissao = models.DateField("data de admissão", null=True, blank=True)
    telefone = models.CharField("telefone / WhatsApp", max_length=20, blank=True)
    whatsapp = models.CharField(
        "celular no formato do WhatsApp", max_length=15, blank=True, db_index=True, editable=False,
        help_text="Preenchido sozinho a partir do telefone (55 + DDD + número). Vazio se não for celular.",
    )
    situacao = models.CharField("situação", max_length=10, choices=Situacao.choices, default=Situacao.ATIVO)
    data_desligamento = models.DateField("data de desligamento", null=True, blank=True)
    inativo_pode_acessar = models.BooleanField(
        "desligado ainda pode pedir documentos no WhatsApp", default=True,
        help_text="Se o funcionário for desligado, ele continua recebendo contracheques antigos pelo assistente?",
    )
    criado_em = models.DateTimeField("cadastrado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "funcionário"
        verbose_name_plural = "funcionários"
        ordering = ["nome_completo"]
        indexes = [models.Index(fields=["contrato", "situacao"])]

    def __str__(self):
        return f"{self.nome_completo} ({self.matricula})"

    def save(self, *args, **kwargs):
        self.whatsapp = normalizar_celular(self.telefone)
        if kwargs.get("update_fields") is not None and "telefone" in kwargs["update_fields"]:
            kwargs["update_fields"] = {*kwargs["update_fields"], "whatsapp"}
        self.cpf = somente_digitos(self.cpf)
        self.matricula = (self.matricula or "").strip()
        self.nome_completo = " ".join((self.nome_completo or "").split())
        super().save(*args, **kwargs)

    @property
    def primeiro_nome(self):
        return self.nome_completo.split(" ", 1)[0].capitalize()

    @property
    def cpf_formatado(self):
        return formatar_cpf(self.cpf)

    @property
    def pode_acessar(self):
        return self.situacao == self.Situacao.ATIVO or self.inativo_pode_acessar
