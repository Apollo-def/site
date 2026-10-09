from django import forms
from django.contrib import admin, messages
from django.shortcuts import render
from django.urls import path

from .importacao import importar_funcionarios
from .models import Funcionario, Instalacao


class FormImportacao(forms.Form):
    arquivo = forms.FileField(label="Planilha (.xlsx)")
    somente_conferir = forms.BooleanField(
        label="Somente conferir (mostra os erros sem salvar nada)", required=False, initial=True)

    def clean_arquivo(self):
        arquivo = self.cleaned_data["arquivo"]
        if not arquivo.name.lower().endswith(".xlsx"):
            raise forms.ValidationError("Envie um arquivo Excel .xlsx.")
        return arquivo


@admin.register(Instalacao)
class InstalacaoAdmin(admin.ModelAdmin):
    list_display = ["nome", "total_funcionarios"]
    search_fields = ["nome"]

    @admin.display(description="funcionários")
    def total_funcionarios(self, obj):
        return obj.funcionarios.count()


@admin.register(Funcionario)
class FuncionarioAdmin(admin.ModelAdmin):
    list_display = ["nome_completo", "matricula", "cpf_formatado", "cargo", "instalacao", "escala", "situacao"]
    list_filter = ["situacao", "escala", "cargo", "instalacao"]
    search_fields = ["nome_completo", "matricula", "cpf"]
    list_per_page = 50
    autocomplete_fields = ["instalacao"]
    readonly_fields = ["criado_em", "atualizado_em"]
    change_list_template = "admin/funcionarios/funcionario/change_list.html"

    @admin.display(description="CPF", ordering="cpf")
    def cpf_formatado(self, obj):
        return obj.cpf_formatado

    def get_urls(self):
        extra = [path("importar/", self.admin_site.admin_view(self.importar), name="funcionarios_importar")]
        return extra + super().get_urls()

    def importar(self, request):
        if not self.has_add_permission(request) or not self.has_change_permission(request):
            messages.error(request, "Você não tem permissão para importar funcionários.")
            form, resultado = FormImportacao(), None
        elif request.method == "POST":
            form = FormImportacao(request.POST, request.FILES)
            resultado = None
            if form.is_valid():
                resultado = importar_funcionarios(form.cleaned_data["arquivo"],
                                                  simulacao=form.cleaned_data["somente_conferir"])
        else:
            form, resultado = FormImportacao(), None
        contexto = {**self.admin_site.each_context(request), "title": "Importar planilha de funcionários",
                    "opts": self.model._meta, "form": form, "resultado": resultado}
        return render(request, "admin/funcionarios/importar.html", contexto)
