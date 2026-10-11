from django.conf import settings
from django.db import models
from django.utils import timezone


class LogAuditoria(models.Model):
    """Registro de quem fez o quê, quando e de onde (IP). Não pode ser editado nem apagado."""

    class Acao(models.TextChoices):
        LOGIN = "login", "Login realizado"
        LOGIN_FALHA = "login_falha", "Tentativa de login errada"
        LOGOUT = "logout", "Saiu do sistema"
        CONTA_BLOQUEADA = "conta_bloqueada", "Conta bloqueada"
        CONTA_DESBLOQUEADA = "conta_desbloqueada", "Conta desbloqueada pelo RH"
        PRIMEIRO_ACESSO = "primeiro_acesso", "Primeiro acesso concluído"
        SENHA_ALTERADA = "senha_alterada", "Senha alterada"
        SENHA_RESETADA = "senha_resetada", "Senha resetada pelo RH"
        VISUALIZOU_DOCUMENTO = "visualizou_documento", "Visualizou documento"
        BAIXOU_DOCUMENTO = "baixou_documento", "Baixou documento"
        ADMIN_ABRIU_ARQUIVO = "admin_abriu_arquivo", "RH abriu arquivo privado"
        IMPORTACAO_FUNCIONARIOS = "importacao_funcionarios", "Importação de funcionários"
        UPLOAD_CONTRACHEQUES = "upload_contracheques", "Upload de contracheques"
        UPLOAD_DOCUMENTOS = "upload_documentos", "Upload de documentos (termo de VT, registro...)"
        SOLICITACAO_CRIADA = "solicitacao_criada", "Solicitação criada"
        SOLICITACAO_STATUS = "solicitacao_status", "Status de solicitação alterado"
        ACEITE_ELETRONICO = "aceite_eletronico", "Aceite eletrônico de termo"
        EXPORTOU_DADOS = "exportou_dados", "Exportou dados para Excel"
        DUAS_ETAPAS_ATIVADA = "duas_etapas_ativada", "Verificação em duas etapas ativada"
        DUAS_ETAPAS_DESATIVADA = "duas_etapas_desativada", "Verificação em duas etapas desativada"
        DUAS_ETAPAS_FALHA = "duas_etapas_falha", "Código da verificação em duas etapas errado"
        WHATSAPP_PRIMEIRO_CONTATO = "whatsapp_primeiro_contato", "WhatsApp: primeiro contato do funcionário"
        WHATSAPP_CADASTRO_LIBERADO = "whatsapp_cadastro_liberado", "WhatsApp: número novo liberado pelo RH"
        # Do tempo em que havia login e conferência de CPF; ficam para o histórico continuar legível.
        WHATSAPP_VERIFICADO = "whatsapp_verificado", "WhatsApp: número verificado"
        WHATSAPP_VERIFICACAO_FALHA = "whatsapp_verificacao_falha", "WhatsApp: verificação errada"
        WHATSAPP_DOCUMENTO_ENVIADO = "whatsapp_documento_enviado", "WhatsApp: documento enviado"
        WHATSAPP_TERMO_RECEBIDO = "whatsapp_termo_recebido", "WhatsApp: termo assinado recebido"
        WHATSAPP_TRANSFERIDO = "whatsapp_transferido", "WhatsApp: conversa transferida para o RH"
        OCORRENCIA_CONFERIDA = "ocorrencia_conferida", "Ocorrência do grupo (falta, atestado...) conferida pelo RH"
        WHATSAPP_VERIFICACAO_RESETADA = "whatsapp_verificacao_resetada", "WhatsApp: verificação resetada pelo RH"

    data_hora = models.DateTimeField("data/hora", auto_now_add=True, db_index=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="usuário", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="logs",
    )
    identificador = models.CharField(
        "login digitado / usuário", max_length=150, blank=True,
        help_text="Guarda o que foi digitado no login (útil em tentativas erradas).",
    )
    acao = models.CharField("ação", max_length=40, choices=Acao.choices, db_index=True)
    descricao = models.TextField("detalhes", blank=True)
    objeto_tipo = models.CharField("tipo do objeto", max_length=60, blank=True)
    objeto_id = models.CharField("id do objeto", max_length=40, blank=True)
    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    user_agent = models.CharField("navegador/aparelho", max_length=300, blank=True)

    class Meta:
        verbose_name = "registro de auditoria"
        verbose_name_plural = "log de auditoria"
        ordering = ["-data_hora"]

    def __str__(self):
        return f"{timezone.localtime(self.data_hora):%d/%m/%Y %H:%M} — {self.get_acao_display()}"
