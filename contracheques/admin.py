from django.contrib import admin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from auditoria.models import LogAuditoria
from portal.admin_lotes import LoteAdminMixin

from . import processamento
from .forms import EnviarContrachequesForm
from .models import Contracheque, LoteContracheque, PaginaNaoIdentificada


@admin.register(LoteContracheque)
class LoteContrachequeAdmin(LoteAdminMixin, admin.ModelAdmin):
    list_display = ("competencia", "tipo", "contrato", "status", "progresso_pct", "total_paginas",
                    "total_distribuidos", "pendentes", "enviado_em", "enviado_por")
    list_filter = ("status", "tipo", "ano", "mes", "contrato")
    list_display_links = ("competencia",)

    # Telas de envio e relatório: portal/admin_lotes.py
    prefixo = "contracheques"
    modelo_pendencia = PaginaNaoIdentificada
    campo_pendencias = "paginas_nao_identificadas"
    campo_entregues = "contracheques"
    form_enviar = EnviarContrachequesForm
    titulo_enviar = "Enviar contracheques do mês"
    template_enviar = "admin/contracheques/lote/enviar.html"
    template_relatorio = "admin/contracheques/lote/relatorio.html"
    acao_auditoria = LogAuditoria.Acao.UPLOAD_CONTRACHEQUES
    nome_item = "contracheque"
    rotulos = {"itens": "contracheques", "Itens": "Contracheques", "chave": "cuja matrícula",
               "sem_dono": "capa, resumo da folha"}
    iniciar_processamento = staticmethod(processamento.iniciar_processamento)
    reentregar_pendencias = staticmethod(processamento.reentregar_pendencias)
    vincular_pendencia = staticmethod(processamento.vincular_pendente)
    descartar_pendencia = staticmethod(processamento.descartar_pendente)

    def criar_lote(self, request, dados):
        lote = LoteContracheque(ano=dados["ano"], mes=dados["mes"], tipo=dados["tipo"], contrato=dados["contrato"],
                                substituir_existentes=dados["substituir"], enviado_por=request.user)
        lote.arquivo.save(f"folha_{dados['ano']}-{dados['mes']:02d}.pdf", dados["arquivo"], save=False)
        lote.save()
        return lote


@admin.register(Contracheque)
class ContrachequeAdmin(admin.ModelAdmin):
    list_display = ("funcionario", "matricula", "contrato", "competencia", "tipo", "vinculado_manualmente", "criado_em")
    list_filter = ("funcionario__contrato", "tipo", "ano", "mes", "vinculado_manualmente")
    search_fields = ("funcionario__nome_completo", "funcionario__matricula", "funcionario__cpf")
    autocomplete_fields = ("funcionario",)
    list_select_related = ("funcionario__contrato",)
    readonly_fields = ("lote", "paginas", "criado_em")

    @admin.display(description="matrícula", ordering="funcionario__matricula")
    def matricula(self, obj):
        return obj.funcionario.matricula

    @admin.display(description="contrato", ordering="funcionario__contrato__sigla")
    def contrato(self, obj):
        return obj.funcionario.contrato

    def has_add_permission(self, request):
        # Contracheques entram pelo "Enviar PDF do mês" (separação automática) ou pelas pendências.
        return False


@admin.register(PaginaNaoIdentificada)
class PaginaNaoIdentificadaAdmin(admin.ModelAdmin):
    list_display = ("lote", "paginas", "matricula_lida", "nome_lido", "motivo", "resolvida", "funcionario")
    list_filter = ("resolvida", "motivo", "lote__contrato", "lote")
    search_fields = ("matricula_lida", "nome_lido")
    readonly_fields = ("lote", "paginas", "matricula_lida", "nome_lido", "motivo", "arquivo", "resolvida",
                       "funcionario", "resolvida_em", "resolvida_por")

    def has_add_permission(self, request):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        pendente = get_object_or_404(PaginaNaoIdentificada, pk=object_id)
        return redirect(reverse("admin:contracheques_lote_relatorio", args=[pendente.lote_id]) + "#pendentes")
