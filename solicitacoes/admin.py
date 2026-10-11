from collections import Counter

from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from auditoria.models import LogAuditoria
from auditoria.servicos import registrar
from portal.planilhas import resposta_excel
from whatsapp.notificacoes import avisar_mudanca_de_status

from .models import HistoricoSolicitacao, ItemFardamento, Solicitacao, TipoSolicitacao


class TipoSolicitacaoForm(forms.ModelForm):
    class Meta:
        model = TipoSolicitacao
        fields = "__all__"

    def clean(self):
        dados = super().clean()
        if not dados.get("para_todos") and not dados.get("contratos"):
            self.add_error("contratos", "Escolha pelo menos um contrato ou marque 'disponível para todos'.")
        return dados


@admin.register(TipoSolicitacao)
class TipoSolicitacaoAdmin(admin.ModelAdmin):
    form = TipoSolicitacaoForm
    list_display = ("nome", "ativo", "para_todos", "ordem")
    list_editable = ("ordem",)
    list_filter = ("ativo", "para_todos", "contratos")
    search_fields = ("nome",)
    filter_horizontal = ("contratos",)
    fieldsets = (
        (None, {"fields": ("nome", "descricao", "ativo", "ordem")}),
        ("Quem pode solicitar", {"fields": ("para_todos", "contratos")}),
    )


class HistoricoInline(admin.TabularInline):
    model = HistoricoSolicitacao
    extra = 0
    can_delete = False
    fields = ("data_hora", "status", "observacao", "usuario")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


class ItemFardamentoInline(admin.TabularInline):
    model = ItemFardamento
    extra = 0
    can_delete = False
    fields = ("peca", "descricao", "tamanho")
    readonly_fields = fields
    verbose_name_plural = "peças de fardamento pedidas"

    def has_add_permission(self, request, obj=None):
        return False


def motivo_do_pedido(solicitacao):
    """O motivo que o funcionário escreveu (guardado na observação como "Motivo: ...")."""
    return solicitacao.observacao_funcionario.partition("Motivo: ")[2].strip()


@admin.register(ItemFardamento)
class ItemFardamentoAdmin(admin.ModelAdmin):
    """Relatório de fardamento: uma linha por peça pedida, com filtros, resumo por tamanho e planilha."""

    list_display = ("pedido_em", "funcionario", "matricula", "contrato", "peca_pedida", "tamanho", "motivo",
                    "situacao", "protocolo")
    list_display_links = None
    list_filter = ("peca", "solicitacao__status", "solicitacao__funcionario__contrato", "solicitacao__criado_em")
    search_fields = ("solicitacao__funcionario__nome_completo", "solicitacao__funcionario__matricula",
                     "solicitacao__protocolo", "tamanho")
    search_help_text = "Busque pelo nome, matrícula, protocolo ou tamanho."
    date_hierarchy = "solicitacao__criado_em"
    list_select_related = ("solicitacao__funcionario__contrato",)
    list_per_page = 100

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # a resposta ao pedido é dada em Solicitações

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description="pedido em", ordering="solicitacao__criado_em")
    def pedido_em(self, obj):
        return timezone.localtime(obj.solicitacao.criado_em).strftime("%d/%m/%Y %H:%M")

    @admin.display(description="funcionário", ordering="solicitacao__funcionario__nome_completo")
    def funcionario(self, obj):
        return obj.solicitacao.funcionario.nome_completo

    @admin.display(description="matrícula", ordering="solicitacao__funcionario__matricula")
    def matricula(self, obj):
        return obj.solicitacao.funcionario.matricula

    @admin.display(description="contrato", ordering="solicitacao__funcionario__contrato__sigla")
    def contrato(self, obj):
        return obj.solicitacao.funcionario.contrato

    @admin.display(description="peça", ordering="peca")
    def peca_pedida(self, obj):
        return obj.nome_da_peca

    @admin.display(description="motivo")
    def motivo(self, obj):
        return motivo_do_pedido(obj.solicitacao)

    @admin.display(description="situação", ordering="solicitacao__status")
    def situacao(self, obj):
        return obj.solicitacao.get_status_display()

    @admin.display(description="protocolo", ordering="solicitacao__protocolo")
    def protocolo(self, obj):
        url = reverse("admin:solicitacoes_solicitacao_change", args=[obj.solicitacao_id])
        return format_html('<a href="{}">{}</a>', url, obj.solicitacao.protocolo)

    def changelist_view(self, request, extra_context=None):
        """Em cima da lista: quantas peças de cada tamanho (com os mesmos filtros), para saber o que comprar."""
        extra_context = extra_context or {}
        try:
            itens = self.get_changelist_instance(request).get_queryset(request)
        except Exception:  # filtro inválido na URL: o Django mostra o aviso dele, sem resumo
            itens = None
        if itens is not None:
            contagem = Counter((item.nome_da_peca, item.tamanho) for item in itens)
            por_peca = {}
            for (peca, tamanho), quantidade in sorted(contagem.items(), key=lambda par: (par[0][0], ordem_tamanho(par[0][1]))):
                por_peca.setdefault(peca, []).append((tamanho, quantidade))
            extra_context["resumo"] = [{"nome": peca, "tamanhos": tamanhos, "total": sum(q for _, q in tamanhos)}
                                       for peca, tamanhos in por_peca.items()]
            extra_context["total_pecas"] = sum(contagem.values())
        return super().changelist_view(request, extra_context)

    def get_urls(self):
        return [path("exportar/", self.admin_site.admin_view(self.exportar_view),
                     name="solicitacoes_itemfardamento_exportar")] + super().get_urls()

    def exportar_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        itens = (self.get_changelist_instance(request).get_queryset(request)
                 .select_related("solicitacao__funcionario__contrato", "solicitacao__atendido_por")
                 .order_by("solicitacao__criado_em", "id"))
        linhas = [[
            i.solicitacao.criado_em, i.solicitacao.protocolo, i.solicitacao.funcionario.nome_completo,
            i.solicitacao.funcionario.matricula, i.solicitacao.funcionario.contrato.sigla,
            i.solicitacao.funcionario.cargo, i.solicitacao.funcionario.instalacao, i.nome_da_peca, i.tamanho,
            motivo_do_pedido(i.solicitacao), i.solicitacao.get_status_display(), i.solicitacao.resposta_rh,
            i.solicitacao.finalizada_em,
        ] for i in itens]
        registrar(LogAuditoria.Acao.EXPORTOU_DADOS, request=request, descricao=f"Fardamento ({len(linhas)} peças)")
        return resposta_excel(
            f"fardamento_{timezone.localdate():%Y-%m-%d}.xlsx", "Fardamento",
            ["Pedido em", "Protocolo", "Funcionário", "Matrícula", "Contrato", "Cargo", "Instalação", "Peça",
             "Tamanho", "Motivo", "Situação", "Resposta do RH", "Finalizado em"],
            linhas)


ORDEM_LETRAS = ["PP", "P", "M", "G", "GG", "XG", "EG", "XGG", "EGG", "G1", "G2", "G3", "G4", "UNICO"]


def ordem_tamanho(tamanho):
    """P antes de M antes de G; números em ordem (38 antes de 40)."""
    if tamanho.isdigit():
        return (1, int(tamanho), "")
    return (0, ORDEM_LETRAS.index(tamanho) if tamanho in ORDEM_LETRAS else 99, tamanho)


@admin.register(Solicitacao)
class SolicitacaoAdmin(admin.ModelAdmin):
    list_display = ("protocolo", "funcionario", "contrato", "tipo", "status", "origem", "criado_em",
                    "data_prevista_vigencia")
    list_filter = ("status", "tipo", "origem", "funcionario__contrato", "criado_em")
    search_fields = ("protocolo", "funcionario__nome_completo", "funcionario__matricula", "funcionario__cpf")
    date_hierarchy = "criado_em"
    autocomplete_fields = ("funcionario",)
    list_select_related = ("funcionario__contrato", "tipo")
    readonly_fields = ("protocolo", "origem", "termo_assinado", "atendido_por", "criado_em", "atualizado_em",
                       "finalizada_em")
    fieldsets = (
        ("Solicitação", {"fields": ("protocolo", "origem", "funcionario", "tipo", "observacao_funcionario",
                                    "criado_em")}),
        ("Termo assinado", {"fields": ("termo_assinado",)}),
        ("Atendimento do RH", {"fields": ("status", "motivo_recusa", "resposta_rh", "data_prevista_vigencia",
                                          "atendido_por", "atualizado_em", "finalizada_em")}),
    )
    inlines = [ItemFardamentoInline, HistoricoInline]

    def get_inlines(self, request, obj):
        # As peças só aparecem nos pedidos de fardamento.
        if obj is not None and obj.tipo.codigo == TipoSolicitacao.FARDAMENTO:
            return [ItemFardamentoInline, HistoricoInline]
        return [HistoricoInline]
    actions = ["exportar_selecionadas"]

    # ------------------------------------------------------------------
    # Exportar para Excel (botão no topo da lista: exporta o que estiver filtrado)
    # ------------------------------------------------------------------
    def get_urls(self):
        extras = [path("exportar/", self.admin_site.admin_view(self.exportar_view),
                       name="solicitacoes_solicitacao_exportar")]
        return extras + super().get_urls()

    def exportar_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        lista = self.get_changelist_instance(request)  # aplica os mesmos filtros e a busca da tela
        return self._planilha(request, lista.get_queryset(request))

    @admin.action(description="Exportar as selecionadas para Excel")
    def exportar_selecionadas(self, request, queryset):
        return self._planilha(request, queryset)

    def _planilha(self, request, queryset):
        pedidos = queryset.select_related("funcionario__contrato", "tipo", "atendido_por").order_by("criado_em")
        opcao_vt = {TipoSolicitacao.VT_UTILIZAR: "Utilizar", TipoSolicitacao.VT_CANCELAR: "Cancelar"}
        linhas = [[
            s.protocolo, s.criado_em, s.funcionario.nome_completo, s.funcionario.matricula, s.funcionario.cpf_formatado,
            s.funcionario.contrato.sigla, s.funcionario.cargo, s.tipo.nome, opcao_vt.get(s.tipo.codigo, ""),
            s.get_status_display(), s.observacao_funcionario, s.resposta_rh, s.motivo_recusa, s.data_prevista_vigencia,
            s.atendido_por.get_username() if s.atendido_por else "", s.finalizada_em,
        ] for s in pedidos]
        registrar(LogAuditoria.Acao.EXPORTOU_DADOS, request=request, descricao=f"Solicitações ({len(linhas)} linhas)")
        return resposta_excel(
            "solicitacoes.xlsx", "Solicitações",
            ["Protocolo", "Aberta em", "Funcionário", "Matrícula", "CPF", "Contrato", "Cargo", "Pedido", "VT",
             "Status", "Observação do funcionário", "Resposta do RH", "Motivo da recusa", "Vale a partir de",
             "Atendido por", "Finalizada em"],
            linhas)

    @admin.display(description="contrato", ordering="funcionario__contrato__sigla")
    def contrato(self, obj):
        return obj.funcionario.contrato

    def save_model(self, request, obj, form, change):
        status_mudou = not change or "status" in form.changed_data
        if change and status_mudou:
            obj.atendido_por = request.user
        super().save_model(request, obj, form, change)
        if status_mudou:
            observacao = obj.motivo_recusa if obj.status == Solicitacao.Status.RECUSADA else obj.resposta_rh
            HistoricoSolicitacao.objects.create(
                solicitacao=obj, status=obj.status, observacao=observacao, usuario=request.user
            )
            registrar(LogAuditoria.Acao.SOLICITACAO_STATUS, request=request, objeto=obj,
                      descricao=f"{obj.protocolo}: {obj.get_status_display()}")
            if change and avisar_mudanca_de_status(obj):
                messages.info(request, "O funcionário foi avisado pelo WhatsApp.")
