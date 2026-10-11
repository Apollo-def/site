"""
Telas do painel do RH para os envios em lote (as mesmas para contracheques e documentos):
enviar o PDF, acompanhar o relatório, reprocessar, entregar pendências, vincular e descartar páginas.

Cada ModelAdmin informa só o que muda (formulário, textos, funções de processamento) e o
jeito de criar o lote (criar_lote). Os nomes das telas seguem o padrão:
    admin:<prefixo>_lote_enviar / _relatorio / _reprocessar / _reentregar
    admin:<prefixo>_pendente_vincular / _descartar
"""
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from auditoria.servicos import registrar
from contas.forms import IdentificarFuncionarioForm


class LoteAdminMixin:
    prefixo = ""                  # ex.: "contracheques"
    modelo_pendencia = None       # modelo das páginas não identificadas
    campo_pendencias = ""         # related_name das pendências no lote
    campo_entregues = ""          # related_name do que foi entregue a partir do lote
    form_enviar = None
    titulo_enviar = ""
    template_enviar = ""
    template_relatorio = ""
    acao_auditoria = None
    nome_item = ""                # "contracheque" / "documento" (usado nas mensagens)
    rotulos = {}                  # textos do relatório (ver templates/admin/lotes/relatorio.html)

    # Funções de processamento de cada módulo (staticmethod na subclasse).
    iniciar_processamento = None
    reentregar_pendencias = None
    vincular_pendencia = None
    descartar_pendencia = None

    def criar_lote(self, request, dados):
        """Cria e salva o lote a partir do formulário de envio. Cada módulo implementa."""
        raise NotImplementedError

    # Colunas da lista de envios
    @admin.display(description="progresso")
    def progresso_pct(self, obj):
        return f"{obj.progresso}%"

    @admin.display(description="pendências")
    def pendentes(self, obj):
        return getattr(obj, self.campo_pendencias).filter(resolvida=False).count()

    # ------------------------------------------------------------------
    def get_urls(self):
        telas = [
            ("enviar/", self.enviar_view, "lote_enviar"),
            ("<int:pk>/relatorio/", self.relatorio_view, "lote_relatorio"),
            ("<int:pk>/reprocessar/", self.reprocessar_view, "lote_reprocessar"),
            ("<int:pk>/reentregar/", self.reentregar_view, "lote_reentregar"),
            ("pendente/<int:pk>/vincular/", self.vincular_view, "pendente_vincular"),
            ("pendente/<int:pk>/descartar/", self.descartar_view, "pendente_descartar"),
        ]
        extras = [path(rota, self.admin_site.admin_view(view), name=f"{self.prefixo}_{nome}")
                  for rota, view, nome in telas]
        return extras + super().get_urls()

    def _url(self, nome):
        return f"admin:{self.prefixo}_{nome}"

    def add_view(self, request, form_url="", extra_context=None):
        return redirect(self._url("lote_enviar"))

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return redirect(self._url("lote_relatorio"), object_id)

    def _exigir(self, request, permissao):
        if not getattr(self, f"has_{permissao}_permission")(request):
            raise PermissionDenied

    def _contexto(self, request, **extra):
        meta = self.model._meta
        urls = {nome: self._url(nome) for nome in ("lote_enviar", "lote_reprocessar", "lote_reentregar",
                                                   "pendente_vincular", "pendente_descartar")}
        urls["lista"] = f"admin:{meta.app_label}_{meta.model_name}_changelist"
        return {**self.admin_site.each_context(request), "opts": meta, "urls": urls, "rotulos": self.rotulos, **extra}

    def _voltar_pendencias(self, lote_id):
        return redirect(reverse(self._url("lote_relatorio"), args=[lote_id]) + "#pendentes")

    # ------------------------------------------------------------------
    def enviar_view(self, request):
        self._exigir(request, "add")
        form = self.form_enviar(request.POST or None, request.FILES or None)
        if request.method == "POST" and form.is_valid():
            lote = self.criar_lote(request, form.cleaned_data)
            registrar(self.acao_auditoria, request=request, objeto=lote,
                      descricao=f"{lote} — arquivo '{form.cleaned_data['arquivo'].name}'")
            self.iniciar_processamento(lote)
            messages.success(request, "PDF recebido! A separação começou; acompanhe o andamento abaixo.")
            return redirect(self._url("lote_relatorio"), lote.pk)
        return TemplateResponse(request, self.template_enviar,
                                self._contexto(request, title=self.titulo_enviar, form=form))

    def relatorio_view(self, request, pk):
        self._exigir(request, "view")
        lote = get_object_or_404(self.model.objects.select_related("enviado_por"), pk=pk)
        pendencias = getattr(lote, self.campo_pendencias)
        em_andamento = lote.status in (self.model.Status.AGUARDANDO, self.model.Status.PROCESSANDO)
        return TemplateResponse(request, self.template_relatorio, self._contexto(
            request, title=str(lote), lote=lote, em_andamento=em_andamento,
            avisos=[a for a in lote.avisos.splitlines() if a.strip()],
            pendentes=pendencias.filter(resolvida=False),
            resolvidas=pendencias.filter(resolvida=True).select_related("funcionario"),
            distribuidos=(getattr(lote, self.campo_entregues).select_related("funcionario__contrato")
                          .order_by("funcionario__nome_completo")),
        ))

    def reprocessar_view(self, request, pk):
        self._exigir(request, "change")
        lote = get_object_or_404(self.model, pk=pk)
        if request.method == "POST" and lote.status in (self.model.Status.ERRO, self.model.Status.PROCESSANDO):
            getattr(lote, self.campo_pendencias).all().delete()
            self.iniciar_processamento(lote)
            messages.info(request, "Processamento reiniciado.")
        return redirect(self._url("lote_relatorio"), pk)

    def reentregar_view(self, request, pk):
        self._exigir(request, "change")
        lote = get_object_or_404(self.model, pk=pk)
        if request.method == "POST":
            entregues, ainda = self.reentregar_pendencias(lote, request.user)
            if entregues:
                registrar(self.acao_auditoria, request=request, objeto=lote,
                          descricao=f"{entregues} pendência(s) entregues após cadastro dos funcionários")
            nivel = messages.SUCCESS if entregues else messages.WARNING
            messages.add_message(request, nivel, f"{entregues} {self.nome_item}(s) entregue(s). "
                                                 f"Pendências que ainda faltam: {ainda}.")
        return self._voltar_pendencias(pk)

    def vincular_view(self, request, pk):
        self._exigir(request, "change")
        pendencia = get_object_or_404(self.modelo_pendencia, pk=pk, resolvida=False)
        if request.method == "POST":
            form = IdentificarFuncionarioForm(request.POST)
            if form.is_valid():
                funcionario = form.funcionario
                self.vincular_pendencia(pendencia, funcionario, request.user)
                registrar(self.acao_auditoria, request=request, objeto=pendencia,
                          descricao=f"Páginas {pendencia.paginas} vinculadas manualmente a "
                                    f"{funcionario.nome_completo} ({funcionario.matricula})")
                messages.success(request, f"Páginas {pendencia.paginas} entregues a {funcionario.nome_completo}.")
            else:
                messages.error(request, " ".join(form.errors["identificador"]))
        return self._voltar_pendencias(pendencia.lote_id)

    def descartar_view(self, request, pk):
        self._exigir(request, "change")
        pendencia = get_object_or_404(self.modelo_pendencia, pk=pk, resolvida=False)
        if request.method == "POST":
            self.descartar_pendencia(pendencia, request.user)
            messages.info(request, f"Páginas {pendencia.paginas} marcadas como 'não são de nenhum funcionário'.")
        return self._voltar_pendencias(pendencia.lote_id)
