from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin
from django.core.exceptions import PermissionDenied
from django.db.models import Exists, OuterRef
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path

from auditoria.models import LogAuditoria
from auditoria.servicos import registrar
from whatsapp.models import Conversa

from .forms import AtualizarEfetivoForm, ImportarFuncionariosForm
from .importacao import COLUNAS, gerar_modelo_planilha, importar_funcionarios
from .importacao_efetivo import atualizar_pelo_efetivo
from .models import ConfiguracaoPortal, Contrato, Funcionario, Usuario
from .servicos import desbloquear
from .validadores import somente_digitos


@admin.register(Contrato)
class ContratoAdmin(admin.ModelAdmin):
    list_display = ("sigla", "nome", "ativo", "total_funcionarios")
    list_filter = ("ativo",)
    search_fields = ("sigla", "nome")

    @admin.display(description="funcionários")
    def total_funcionarios(self, obj):
        return obj.funcionarios.count()


class WhatsappFiltro(admin.SimpleListFilter):
    title = "WhatsApp"
    parameter_name = "whatsapp"

    def lookups(self, request, model_admin):
        return [("sem", "Sem celular (não é atendido)"), ("com", "Com celular"), ("conversou", "Já conversou")]

    def queryset(self, request, queryset):
        if self.value() == "sem":
            return queryset.filter(whatsapp="")
        if self.value() == "com":
            return queryset.exclude(whatsapp="")
        if self.value() == "conversou":
            return queryset.filter(ja_conversou=True)
        return queryset


@admin.register(Funcionario)
class FuncionarioAdmin(admin.ModelAdmin):
    list_display = ("nome_completo", "matricula", "cpf_formatado", "contrato", "cargo", "instalacao", "situacao",
                    "status_whatsapp")
    list_filter = ("contrato", "situacao", WhatsappFiltro, "cargo", "inativo_pode_acessar")
    search_fields = ("nome_completo", "matricula", "cpf", "telefone", "instalacao")
    search_help_text = "Busque pelo nome, matrícula, CPF, telefone ou instalação."
    list_select_related = ("contrato",)
    list_per_page = 50
    readonly_fields = ("status_whatsapp", "criado_em", "atualizado_em")
    fieldsets = (
        ("Dados pessoais", {"fields": ("nome_completo", "cpf", "data_nascimento")}),
        ("WhatsApp", {"fields": ("telefone", "status_whatsapp"),
                      "description": "O assistente reconhece o funcionário por este celular e só entrega "
                                     "documentos para ele. Confira com cuidado."}),
        ("Dados funcionais", {"fields": ("matricula", "contrato", "cargo", "instalacao", "data_admissao")}),
        ("Situação", {"fields": ("situacao", "data_desligamento", "inativo_pode_acessar", "criado_em",
                                 "atualizado_em")}),
    )

    @admin.display(description="CPF", ordering="cpf")
    def cpf_formatado(self, obj):
        return obj.cpf_formatado

    def get_queryset(self, request):
        conversou = Conversa.objects.filter(funcionario=OuterRef("pk"), simulada=False)
        return super().get_queryset(request).annotate(ja_conversou=Exists(conversou))

    @admin.display(description="WhatsApp")
    def status_whatsapp(self, obj):
        if not obj.whatsapp:
            return "— sem celular" if not obj.telefone else "⚠️ não é celular"
        return "💬 já conversou" if getattr(obj, "ja_conversou", False) else "📱 cadastrado"

    def get_search_results(self, request, queryset, search_term):
        # Permite buscar CPF digitado com pontuação (ex.: 123.456.789-09).
        resultado, tem_duplicados = super().get_search_results(request, queryset, search_term)
        digitos = somente_digitos(search_term)
        if digitos and digitos != search_term.strip():
            resultado |= queryset.filter(cpf__contains=digitos)
        return resultado, tem_duplicados

    # ------------------------------------------------------------------
    # Importação por planilha (botões no topo da lista de funcionários)
    # ------------------------------------------------------------------
    def get_urls(self):
        extras = [
            path("importar/", self.admin_site.admin_view(self.importar_view), name="contas_funcionario_importar"),
            path("modelo-planilha/", self.admin_site.admin_view(self.modelo_planilha_view),
                 name="contas_funcionario_modelo_planilha"),
            path("atualizar-efetivo/", self.admin_site.admin_view(self.atualizar_efetivo_view),
                 name="contas_funcionario_atualizar_efetivo"),
        ]
        return extras + super().get_urls()

    def _conferir_permissao(self, request):
        if not (self.has_add_permission(request) and self.has_change_permission(request)):
            raise PermissionDenied

    def importar_view(self, request):
        self._conferir_permissao(request)
        resultado = None
        if request.method == "POST":
            form = ImportarFuncionariosForm(request.POST, request.FILES)
            if form.is_valid():
                arquivo = form.cleaned_data["arquivo"]
                resultado = importar_funcionarios(
                    arquivo, arquivo.name,
                    criar_contratos=form.cleaned_data["criar_contratos"],
                    simulacao=form.cleaned_data["somente_conferir"],
                    contrato_padrao=form.cleaned_data["contrato_padrao"],
                )
                registrar(LogAuditoria.Acao.IMPORTACAO_FUNCIONARIOS, request=request,
                          descricao=f"{arquivo.name}: {resultado.erro_geral or resultado.resumo()}")
        else:
            form = ImportarFuncionariosForm()

        contexto = {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "title": "Importar funcionários por planilha",
            "form": form,
            "resultado": resultado,
            "colunas": [(titulo, obrigatoria) for titulo, obrigatoria, _ in COLUNAS.values()],
        }
        return TemplateResponse(request, "admin/contas/funcionario/importar.html", contexto)

    def atualizar_efetivo_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied
        resultado = None
        if request.method == "POST":
            form = AtualizarEfetivoForm(request.POST, request.FILES)
            if form.is_valid():
                arquivo = form.cleaned_data["arquivo"]
                resultado = atualizar_pelo_efetivo(arquivo, arquivo.name,
                                                   simulacao=form.cleaned_data["somente_conferir"])
                if not resultado.simulacao:
                    registrar(LogAuditoria.Acao.IMPORTACAO_FUNCIONARIOS, request=request,
                              descricao=f"Efetivo {arquivo.name}: {resultado.erro_geral or resultado.resumo()}")
        else:
            form = AtualizarEfetivoForm()
        return TemplateResponse(request, "admin/contas/funcionario/atualizar_efetivo.html", {
            **self.admin_site.each_context(request), "opts": self.model._meta,
            "title": "Atualizar celulares pela planilha do Efetivo", "form": form, "resultado": resultado,
        })

    def modelo_planilha_view(self, request):
        self._conferir_permissao(request)
        resposta = HttpResponse(
            gerar_modelo_planilha(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resposta["Content-Disposition"] = 'attachment; filename="modelo_importacao_funcionarios.xlsx"'
        return resposta


@admin.register(ConfiguracaoPortal)
class ConfiguracaoPortalAdmin(admin.ModelAdmin):
    fieldsets = (
        ("Assistente do Colaborador (WhatsApp)", {"fields": (
            "whatsapp_ativo", "whatsapp_numero_publico", "whatsapp_boas_vindas", "whatsapp_aviso_privacidade",
            "whatsapp_horario_atendimento", "whatsapp_opcao_duvidas", "whatsapp_texto_curriculo",
            "whatsapp_forma_envio", "termo_vt_padrao",
            "whatsapp_guardar_dias")}),
        ("Segurança do painel do RH", {"fields": ("maximo_tentativas", "atualizado_em")}),
    )
    readonly_fields = ("atualizado_em",)

    def has_add_permission(self, request):
        return not ConfiguracaoPortal.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # Existe uma configuração só: abre direto nela.
        config = ConfiguracaoPortal.atual()
        return redirect("admin:contas_configuracaoportal_change", config.pk)


@admin.register(Usuario)
class UsuarioAdmin(UserAdmin):
    list_display = ("username", "first_name", "last_name", "is_staff", "is_superuser", "is_active", "duas_etapas_ativa", "bloqueado",
                    "last_login")
    list_filter = ("is_staff", "is_superuser", "is_active", "bloqueado")
    fieldsets = UserAdmin.fieldsets + (
        ("Segurança", {"fields": ("duas_etapas_ativa", "tentativas_falhas", "bloqueado", "bloqueado_em"),
                       "description": "Desmarque ""verificação em duas etapas ativa"" só se a pessoa perdeu o celular "
                                      "(ela ativa de novo no próximo login)."}),
    )
    actions = ["desbloquear_login"]

    def get_queryset(self, request):
        # Só os logins do RH: os antigos logins dos funcionários ficam fora da lista (guardados só para a auditoria).
        return super().get_queryset(request).filter(funcionario__isnull=True)

    @admin.action(description="Desbloquear login dos usuários selecionados")
    def desbloquear_login(self, request, queryset):
        total = 0
        for usuario in queryset.filter(bloqueado=True):
            desbloquear(usuario)
            registrar(LogAuditoria.Acao.CONTA_DESBLOQUEADA, request=request, objeto=usuario,
                      descricao=usuario.get_username())
            total += 1
        self.message_user(request, f"{total} login(s) desbloqueado(s)." if total else
                          "Nenhum dos selecionados estava bloqueado.", messages.SUCCESS if total else messages.WARNING)
