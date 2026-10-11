from django import forms
from django.contrib import admin

from .models import Anexo, Categoria, Orientacao, VersaoAnteriorAnexo


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nome", "descricao", "ordem", "ativa")
    list_editable = ("ordem", "ativa")
    search_fields = ("nome",)


class AnexoInline(admin.TabularInline):
    model = Anexo
    extra = 1
    fields = ("titulo", "arquivo", "versao", "ordem", "atualizado_em")
    readonly_fields = ("versao", "atualizado_em")
    show_change_link = True  # abre o anexo para ver o histórico de versões


class OrientacaoForm(forms.ModelForm):
    class Meta:
        model = Orientacao
        fields = "__all__"

    def clean(self):
        dados = super().clean()
        if not dados.get("para_todos") and not dados.get("contratos"):
            self.add_error("contratos", "Escolha pelo menos um contrato ou marque 'publicar para todos'.")
        return dados


@admin.register(Orientacao)
class OrientacaoAdmin(admin.ModelAdmin):
    form = OrientacaoForm
    list_display = ("titulo", "tipo", "categoria", "status", "publico", "publicado_em", "atualizado_em")
    list_filter = ("status", "tipo", "categoria", "para_todos", "contratos")
    search_fields = ("titulo", "resumo", "conteudo")
    filter_horizontal = ("contratos",)
    readonly_fields = ("criado_por", "criado_em", "publicado_em", "atualizado_em")
    inlines = [AnexoInline]
    fieldsets = (
        (None, {"fields": ("tipo", "titulo", "categoria", "resumo", "conteudo", "tipo_solicitacao", "ordem")}),
        ("Publicação", {"fields": ("status", "para_todos", "contratos")}),
        ("Datas", {"fields": ("criado_por", "criado_em", "publicado_em", "atualizado_em")}),
    )

    @admin.display(description="público")
    def publico(self, obj):
        if obj.para_todos:
            return "Todos"
        return ", ".join(c.sigla for c in obj.contratos.all()) or "—"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("categoria").prefetch_related("contratos")

    def save_model(self, request, obj, form, change):
        if not change:
            obj.criado_por = request.user
        elif {"titulo", "resumo", "conteudo"} & set(form.changed_data):
            obj.marcar_como_atualizada()
        super().save_model(request, obj, form, change)


class VersaoAnteriorInline(admin.TabularInline):
    model = VersaoAnteriorAnexo
    extra = 0
    can_delete = False
    fields = ("versao", "arquivo", "enviado_em", "substituido_em")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Anexo)
class AnexoAdmin(admin.ModelAdmin):
    list_display = ("titulo", "orientacao", "versao", "atualizado_em")
    list_filter = ("orientacao__categoria",)
    search_fields = ("titulo", "orientacao__titulo")
    readonly_fields = ("versao", "atualizado_em")
    inlines = [VersaoAnteriorInline]
