from django.contrib import admin

from .models import LogAuditoria


@admin.register(LogAuditoria)
class LogAuditoriaAdmin(admin.ModelAdmin):
    list_display = ("data_hora", "acao", "identificador", "contrato", "objeto_tipo", "objeto_id", "ip")
    list_filter = ("acao", "data_hora", ("usuario__funcionario__contrato", admin.RelatedOnlyFieldListFilter))
    search_fields = ("identificador", "descricao", "ip", "usuario__funcionario__nome_completo",
                     "usuario__funcionario__matricula", "usuario__funcionario__cpf")
    date_hierarchy = "data_hora"
    list_select_related = ("usuario__funcionario__contrato",)
    list_per_page = 100

    @admin.display(description="contrato")
    def contrato(self, obj):
        funcionario = getattr(obj.usuario, "funcionario", None) if obj.usuario else None
        return funcionario.contrato if funcionario else "—"

    # O log é somente leitura: ninguém cria, altera ou apaga registros pelo painel.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
