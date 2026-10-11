from collections import Counter

from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from auditoria.models import LogAuditoria
from auditoria.servicos import registrar
from contas.models import Contrato
from portal.planilhas import resposta_excel, resposta_excel_abas

from . import relatorio
from .leitura import encontrar_funcionario
from .models import Ocorrencia


class OkDoAdmFiltro(admin.SimpleListFilter):
    title = "OK do ADM (👍 no grupo)"
    parameter_name = "ok_adm"

    def lookups(self, request, model_admin):
        return [("sim", "Com 👍 do ADM"), ("nao", "Ainda sem 👍")]

    def queryset(self, request, queryset):
        if self.value() == "sim":
            return queryset.filter(ok_adm_em__isnull=False)
        if self.value() == "nao":
            return queryset.filter(ok_adm_em__isnull=True)
        return queryset


@admin.register(Ocorrencia)
class OcorrenciaAdmin(admin.ModelAdmin):
    """Relatório do grupo de faltas, atestados, coberturas e extras, e conferência do que ficou em dúvida."""

    list_display = ("data", "tipo", "funcionario_", "escola", "cobrindo", "atestado", "supervisor_nome",
                    "ok_adm", "situacao_", "anexo_")
    list_filter = ("situacao", OkDoAdmFiltro, "tipo", "data", "funcionario__contrato")
    date_hierarchy = "data"
    search_fields = ("nome_informado", "coberto_nome_informado", "escola_informada", "funcionario__nome_completo",
                     "funcionario__matricula", "coberto__nome_completo", "supervisor_nome")
    search_help_text = "Busque pelo nome do funcionário, matrícula, escola ou supervisor."
    autocomplete_fields = ("funcionario", "coberto")
    list_select_related = ("funcionario", "coberto")
    list_per_page = 100
    readonly_fields = ("texto_original", "anexo_link", "supervisor_nome", "recebida_em", "motivo_conferir",
                       "candidatos", "nome_informado", "escola_informada", "coberto_nome_informado", "conferida_por",
                       "conferida_em", "ok_adm_em", "ok_adm_emoji")
    fieldsets = (
        ("O que chegou no grupo", {"fields": ("texto_original", "anexo_link", "supervisor_nome", "recebida_em")}),
        ("O que conferir", {"fields": ("motivo_conferir", "candidatos")}),
        ("Ocorrência", {"fields": ("tipo", "data", ("funcionario", "nome_informado"), "escola_informada",
                                   ("coberto", "coberto_nome_informado"), ("atestado_inicio", "atestado_fim"),
                                   "observacao")}),
        ("Situação", {"fields": ("situacao", "conferida_por", "conferida_em", ("ok_adm_em", "ok_adm_emoji"))}),
    )

    def has_add_permission(self, request):
        return False  # as ocorrências chegam pelo grupo do WhatsApp

    # --- Colunas da lista ---------------------------------------------------
    @admin.display(description="funcionário", ordering="funcionario__nome_completo")
    def funcionario_(self, obj):
        if obj.funcionario_id:
            return f"{obj.funcionario.nome_completo} ({obj.funcionario.matricula})"
        return format_html('⚠️ <em>"{}"</em>', obj.nome_informado)

    @admin.display(description="escola/posto")
    def escola(self, obj):
        return obj.escola_informada or (obj.funcionario.instalacao if obj.funcionario_id else "")

    @admin.display(description="cobrindo")
    def cobrindo(self, obj):
        if obj.coberto_id:
            return obj.coberto.nome_completo
        return format_html('⚠️ <em>"{}"</em>', obj.coberto_nome_informado) if obj.coberto_nome_informado else ""

    @admin.display(description="atestado")
    def atestado(self, obj):
        if not obj.atestado_inicio:
            return ""
        if obj.atestado_fim and obj.atestado_fim != obj.atestado_inicio:
            return f"{obj.atestado_inicio:%d/%m} a {obj.atestado_fim:%d/%m}"
        return f"{obj.atestado_inicio:%d/%m}"

    @admin.display(description="OK do ADM", ordering="ok_adm_em")
    def ok_adm(self, obj):
        if not obj.ok_adm_em:
            return "—"
        return format_html('{} <small>{}</small>', obj.ok_adm_emoji or "👍",
                           timezone.localtime(obj.ok_adm_em).strftime("%d/%m %H:%M"))

    @admin.display(description="situação", ordering="situacao")
    def situacao_(self, obj):
        if obj.situacao == Ocorrencia.Situacao.CONFERIR:
            return format_html('<strong style="color:#b45309">Conferir</strong><br><small>{}</small>',
                               obj.motivo_conferir)
        return "✅ Conferida"

    @admin.display(description="anexo")
    def anexo_(self, obj):
        return self.anexo_link(obj) if obj.anexo else ""

    # --- Tela de conferência ------------------------------------------------
    @admin.display(description="foto/arquivo")
    def anexo_link(self, obj):
        if not obj.anexo:
            return "—"
        return format_html('<a href="{}" target="_blank" rel="noopener">📎 abrir</a>',
                           reverse("arquivo_privado_admin", args=[obj.anexo.name]))

    @admin.display(description="quem pode ser")
    def candidatos(self, obj):
        linhas = []
        for rotulo, nome in (("Funcionário", obj.nome_informado), ("Cobrindo", obj.coberto_nome_informado)):
            if not nome:
                continue
            _, _, achados = encontrar_funcionario(nome, obj.escola_informada)
            descricao = format_html_join(
                "", "<li>{} — matrícula {} — {}</li>",
                ((f.nome_completo, f.matricula, f.instalacao or "sem escola no cadastro") for f in achados[:10]))
            linhas.append(format_html("<strong>{} (\"{}\")</strong><ul>{}</ul>", rotulo, nome,
                                      descricao or format_html("<li>ninguém parecido no cadastro</li>")))
        return format_html_join("", "{}", ((linha,) for linha in linhas)) or "—"

    def save_model(self, request, obj, form, change):
        resolvido = obj.funcionario_id and obj.tipo and (obj.coberto_id or not obj.coberto_nome_informado)
        if "situacao" not in form.changed_data and resolvido:
            obj.situacao = Ocorrencia.Situacao.OK  # o RH escolheu quem é: conferida
        if obj.situacao == Ocorrencia.Situacao.OK and (form.changed_data or not obj.conferida_por_id):
            obj.conferida_por, obj.conferida_em = request.user, timezone.now()
        super().save_model(request, obj, form, change)
        registrar(LogAuditoria.Acao.OCORRENCIA_CONFERIDA, request=request, objeto=obj,
                  descricao=f"Ocorrência conferida: {obj}")

    # --- Resumo e planilha --------------------------------------------------
    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        try:
            filtradas = self.get_changelist_instance(request).get_queryset(request)
        except Exception:  # filtro inválido na URL: o Django mostra o aviso dele
            filtradas = None
        if filtradas is not None:
            por_tipo = Counter(filtradas.values_list("tipo", flat=True))
            extra_context["resumo"] = [(rotulo, por_tipo.get(valor, 0)) for valor, rotulo in Ocorrencia.Tipo.choices]
            extra_context["a_conferir"] = filtradas.filter(situacao=Ocorrencia.Situacao.CONFERIR).count()
            extra_context["total"] = sum(por_tipo.values())
        return super().changelist_view(request, extra_context)

    def get_urls(self):
        return [path("exportar/", self.admin_site.admin_view(self.exportar_view),
                     name="ocorrencias_ocorrencia_exportar"),
                path("relatorio/", self.admin_site.admin_view(self.relatorio_view),
                     name="ocorrencias_ocorrencia_relatorio")] + super().get_urls()

    def relatorio_view(self, request):
        """Relatório detalhado do período: totais, por funcionário, por escola, por dia e por supervisor."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        inicio, fim = relatorio.periodo_padrao(timezone.localdate())
        de = relatorio.ler_data(request.GET.get("de", ""), inicio)
        ate = relatorio.ler_data(request.GET.get("ate", ""), fim)
        tipo = request.GET.get("tipo", "") if request.GET.get("tipo", "") in relatorio.TIPOS else ""
        contrato = request.GET.get("contrato", "") if request.GET.get("contrato", "").isdigit() else ""
        escola = request.GET.get("escola", "").strip()[:100]
        ocorrencias = list(relatorio.filtrar(de, ate, tipo, contrato, escola))
        dados = relatorio.montar(ocorrencias)
        if request.GET.get("formato") == "excel":
            registrar(LogAuditoria.Acao.EXPORTOU_DADOS, request=request,
                      descricao=f"Relatório de ocorrências {de:%d/%m/%Y} a {ate:%d/%m/%Y} ({len(ocorrencias)} linhas)")
            return resposta_excel_abas(f"relatorio_ocorrencias_{de:%Y-%m-%d}_a_{ate:%Y-%m-%d}.xlsx",
                                       relatorio.abas_excel(ocorrencias, dados))
        return TemplateResponse(request, "admin/ocorrencias/relatorio.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Relatório de faltas, atestados, coberturas e extras", "dados": dados,
            "de": de, "ate": ate, "tipo": tipo, "contrato": contrato, "escola": escola,
            "tipos": Ocorrencia.Tipo.choices, "contratos": Contrato.objects.filter(ativo=True),
            "consulta": request.GET.urlencode(),
        })

    def exportar_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        ocorrencias = (self.get_changelist_instance(request).get_queryset(request)
                       .select_related("funcionario__contrato", "coberto").order_by("data", "recebida_em"))
        linhas = [[
            o.data, o.get_tipo_display(),
            o.funcionario.nome_completo if o.funcionario_id else f"(conferir) {o.nome_informado}",
            o.funcionario.matricula if o.funcionario_id else "",
            o.funcionario.contrato.sigla if o.funcionario_id else "",
            o.escola_informada or (o.funcionario.instalacao if o.funcionario_id else ""),
            o.coberto.nome_completo if o.coberto_id else o.coberto_nome_informado,
            o.coberto.matricula if o.coberto_id else "",
            o.atestado_inicio, o.atestado_fim, o.observacao, o.supervisor_nome, o.ok_adm_em,
            o.get_situacao_display(), o.recebida_em, o.texto_original,
        ] for o in ocorrencias]
        registrar(LogAuditoria.Acao.EXPORTOU_DADOS, request=request, descricao=f"Ocorrências ({len(linhas)} linhas)")
        return resposta_excel(
            f"ocorrencias_{timezone.localdate():%Y-%m-%d}.xlsx", "Ocorrências",
            ["Data", "Tipo", "Funcionário", "Matrícula", "Contrato", "Escola/posto", "Cobrindo", "Matrícula coberto",
             "Atestado de", "Atestado até", "Observação", "Enviada por", "OK do ADM (👍) em", "Situação",
             "Recebida em", "Mensagem original"],
            linhas)
