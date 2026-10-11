from django.contrib import admin

from auditoria.models import LogAuditoria
from portal.admin_lotes import LoteAdminMixin

from . import processamento
from .forms import EnviarDocumentosForm
from .models import DocumentoFuncionario, LoteDocumento, PendenciaDocumento


@admin.register(DocumentoFuncionario)
class DocumentoFuncionarioAdmin(admin.ModelAdmin):
    list_display = ("titulo", "tipo", "funcionario", "contrato", "enviado_em", "enviado_por")
    list_filter = ("tipo", "funcionario__contrato", "vinculado_manualmente", "enviado_em")
    search_fields = ("titulo", "funcionario__nome_completo", "funcionario__matricula", "funcionario__cpf")
    autocomplete_fields = ("funcionario",)
    list_select_related = ("funcionario__contrato", "enviado_por")
    readonly_fields = ("lote", "paginas", "vinculado_manualmente", "enviado_por", "enviado_em")

    @admin.display(description="contrato", ordering="funcionario__contrato__sigla")
    def contrato(self, obj):
        return obj.funcionario.contrato

    def save_model(self, request, obj, form, change):
        if not change:
            obj.enviado_por = request.user
        super().save_model(request, obj, form, change)


@admin.register(LoteDocumento)
class LoteDocumentoAdmin(LoteAdminMixin, admin.ModelAdmin):
    list_display = ("titulo", "tipo", "status", "progresso_pct", "total_paginas", "total_distribuidos",
                    "pendentes", "enviado_em", "enviado_por")
    list_filter = ("status", "tipo")

    # Telas de envio e relatório: portal/admin_lotes.py
    prefixo = "documentos"
    modelo_pendencia = PendenciaDocumento
    campo_pendencias = "pendencias"
    campo_entregues = "documentos"
    form_enviar = EnviarDocumentosForm
    titulo_enviar = "Enviar documento para todos os funcionários"
    template_enviar = "admin/documentos/lote/enviar.html"
    template_relatorio = "admin/documentos/lote/relatorio.html"
    acao_auditoria = LogAuditoria.Acao.UPLOAD_DOCUMENTOS
    nome_item = "documento"
    rotulos = {"itens": "documentos", "Itens": "Documentos", "chave": "cujo CPF",
               "sem_dono": "capa, página em branco"}
    iniciar_processamento = staticmethod(processamento.iniciar_processamento)
    reentregar_pendencias = staticmethod(processamento.reentregar_pendencias)
    vincular_pendencia = staticmethod(processamento.vincular_pendencia)
    descartar_pendencia = staticmethod(processamento.descartar_pendencia)

    def criar_lote(self, request, dados):
        lote = LoteDocumento(tipo=dados["tipo"], titulo=dados["titulo"], enviado_por=request.user)
        lote.arquivo.save(f"{dados['tipo']}.pdf", dados["arquivo"], save=False)
        lote.save()
        return lote
