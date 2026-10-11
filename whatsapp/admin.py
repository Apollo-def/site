from urllib.parse import quote

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Case, IntegerField, Q, Value, When
from django.shortcuts import get_object_or_404, redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone

from contas.models import ConfiguracaoPortal, Funcionario
from contas.validadores import formatar_celular
from portal.lotes import nomes_compativeis

from . import ia
from .atendimento import receber
from .cadastro import ErroCadastro, liberar, recusar
from .canal import CanalGateway, ErroCanal, canal_para, gateway_configurado, meta_configurada
from .models import Conversa, Mensagem, PedidoCadastro, PerguntaSemResposta
from .numeros import mascarar, normalizar_celular, somente_digitos

LIMITE_BUSCA = 20


def buscar_funcionarios(termo):
    """Funcionários pelo nome (todas as palavras), matrícula, CPF ou celular."""
    termo = " ".join((termo or "").split())
    digitos = somente_digitos(termo)
    consulta = Funcionario.objects.select_related("contrato").order_by("nome_completo")
    if not termo:
        return consulta.none()
    if digitos and len(digitos) >= len(termo.replace(" ", "")) - 4:  # o texto é praticamente só números
        filtro = Q(matricula=termo) | Q(matricula=digitos)
        if len(digitos) == 11:
            filtro |= Q(cpf=digitos)
        celular = normalizar_celular(digitos)
        if celular:
            filtro |= Q(whatsapp=celular)
        return consulta.filter(filtro)[:LIMITE_BUSCA]
    filtro = Q()
    for palavra in termo.split():
        filtro &= Q(nome_completo__icontains=palavra)
    return consulta.filter(filtro)[:LIMITE_BUSCA]


@admin.register(Conversa)
class ConversaAdmin(admin.ModelAdmin):
    list_display = ("nome_funcionario", "matricula", "contrato", "numero_mascarado", "estado",
                    "aguardando_humano_desde", "atualizada_em", "simulada")
    list_display_links = ("nome_funcionario",)
    list_filter = ("estado", "simulada", "funcionario__contrato")
    search_fields = ("funcionario__nome_completo", "funcionario__matricula", "funcionario__cpf", "numero")
    search_help_text = "Busque pelo nome, matrícula, CPF ou celular (só números)."
    list_select_related = ("funcionario__contrato",)

    @admin.display(description="funcionário", ordering="funcionario__nome_completo")
    def nome_funcionario(self, obj):
        return obj.funcionario.nome_completo if obj.funcionario_id else "— número fora do cadastro"

    @admin.display(description="matrícula", ordering="funcionario__matricula")
    def matricula(self, obj):
        return obj.funcionario.matricula if obj.funcionario_id else "—"

    @admin.display(description="contrato", ordering="funcionario__contrato__sigla")
    def contrato(self, obj):
        return obj.funcionario.contrato if obj.funcionario_id else "—"

    @admin.display(description="número", ordering="numero")
    def numero_mascarado(self, obj):
        return mascarar(obj.numero)

    def get_queryset(self, request):
        # As que esperam uma pessoa do RH aparecem primeiro.
        return super().get_queryset(request).annotate(
            prioridade=Case(When(estado=Conversa.Estado.HUMANO, then=Value(0)), default=Value(1),
                            output_field=IntegerField())).order_by("prioridade", "-atualizada_em")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def pode_atender(self, request):
        return request.user.has_perm("whatsapp.atender_whatsapp")

    def get_urls(self):
        extras = [
            path("simulador/", self.admin_site.admin_view(self.simulador_view), name="whatsapp_simulador"),
            path("conexao/", self.admin_site.admin_view(self.conexao_view), name="whatsapp_conexao"),
            path("<int:pk>/atender/", self.admin_site.admin_view(self.atender_view), name="whatsapp_atender"),
        ]
        return extras + super().get_urls()

    def _contexto(self, request, **extra):
        return {**self.admin_site.each_context(request), "opts": self.model._meta, **extra}

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return redirect("admin:whatsapp_atender", object_id)

    # ------------------------------------------------------------------
    # Atendimento humano: ler a conversa e responder pelo painel
    # ------------------------------------------------------------------
    def atender_view(self, request, pk):
        if not self.has_view_permission(request):
            raise PermissionDenied
        conversa = get_object_or_404(Conversa.objects.select_related("funcionario__contrato", "atendente"), pk=pk)
        if request.method == "POST":
            if not self.pode_atender(request):
                raise PermissionDenied
            if "devolver" in request.POST:
                conversa.estado = Conversa.Estado.ATENDIMENTO
                conversa.aguardando_humano_desde = None
                conversa.contexto = {}
                conversa.save(update_fields=["estado", "aguardando_humano_desde", "contexto", "atualizada_em"])
                messages.success(request, "Conversa devolvida ao assistente.")
            elif request.POST.get("texto", "").strip():
                texto = request.POST["texto"].strip()
                mensagem = Mensagem(conversa=conversa, direcao=Mensagem.Direcao.SAIDA, autor=Mensagem.Autor.RH,
                                    texto=texto, enviado_por=request.user)
                try:
                    mensagem.id_externo = canal_para(conversa).enviar_aviso(conversa, texto)
                except ErroCanal as erro:
                    mensagem.falhou = True
                    messages.error(request, f"A mensagem não saiu: {erro}")
                mensagem.save()
                conversa.atendente = request.user
                conversa.save(update_fields=["atendente", "atualizada_em"])
                if not mensagem.falhou:
                    messages.success(request, "Mensagem enviada.")
            return redirect("admin:whatsapp_atender", pk)
        return TemplateResponse(request, "admin/whatsapp/atender.html", self._contexto(
            request, title=str(conversa), conversa=conversa, mensagens=conversa.mensagens.select_related("enviado_por"),
            pode_atender=self.pode_atender(request), numero=mascarar(conversa.numero),
        ))

    # ------------------------------------------------------------------
    # Simulador: o RH conversa com o assistente como se fosse o funcionário
    # ------------------------------------------------------------------
    def simulador_view(self, request):
        if not self.pode_atender(request):
            raise PermissionDenied
        busca = request.GET.get("busca", "").strip()
        encontrados = list(buscar_funcionarios(busca)) if busca else []
        if busca and len(encontrados) == 1 and encontrados[0].whatsapp:  # achou uma pessoa só: já abre a conversa
            return redirect(f"{reverse('admin:whatsapp_simulador')}?numero={encontrados[0].whatsapp}")
        numero = normalizar_celular(request.GET.get("numero") or request.POST.get("numero") or "")
        if request.method == "POST" and numero:
            if "reiniciar" in request.POST:
                Conversa.objects.filter(numero=numero, simulada=True).delete()
                messages.info(request, "Conversa do simulador apagada. A próxima mensagem começa do zero.")
            else:
                arquivo = request.FILES.get("arquivo")
                midia = {"conteudo": arquivo.read(), "nome": arquivo.name[:150]} if arquivo else None
                receber(numero, texto=request.POST.get("texto", "").strip(), midia=midia, simulada=True)
            return redirect(f"{reverse('admin:whatsapp_simulador')}?numero={numero}")
        conversa = Conversa.objects.filter(numero=numero, simulada=True).first() if numero else None
        return TemplateResponse(request, "admin/whatsapp/simulador.html", self._contexto(
            request, title="Simulador do WhatsApp", numero=numero, conversa=conversa, busca=busca,
            encontrados=encontrados, limite_busca=LIMITE_BUSCA,
            mensagens=conversa.mensagens.all() if conversa else [],
            exemplos=Funcionario.objects.exclude(whatsapp="").select_related("contrato")[:15],
            dono=Funcionario.objects.filter(whatsapp=numero).first() if numero else None,
            canal_ligado=ConfiguracaoPortal.atual().whatsapp_ativo,
        ))

    # ------------------------------------------------------------------
    # Conexão: QR code do gateway (ou o que falta configurar na Meta)
    # ------------------------------------------------------------------
    def conexao_view(self, request):
        if not request.user.is_superuser:
            raise PermissionDenied
        modo_gateway = settings.WHATSAPP_CANAL == "gateway"
        if request.method == "POST" and modo_gateway and "desconectar" in request.POST:
            try:
                CanalGateway().desconectar()
                messages.success(request, "WhatsApp desconectado. Em alguns segundos aparece um novo QR code.")
            except ErroCanal as erro:
                messages.error(request, f"Não consegui desconectar: {erro}")
            return redirect("admin:whatsapp_conexao")
        situacao, erro_gateway = {}, ""
        if modo_gateway and gateway_configurado():
            try:
                situacao = CanalGateway().situacao()
            except ErroCanal as erro:
                erro_gateway = str(erro)
        ultima = (Mensagem.objects.filter(conversa__simulada=False, direcao=Mensagem.Direcao.ENTRADA)
                  .order_by("-criada_em").first())
        if modo_gateway:
            itens = [
                ("Atendimento ligado (Configuração do portal)", ConfiguracaoPortal.atual().whatsapp_ativo),
                ("Senha do gateway (WHATSAPP_GATEWAY_TOKEN, 20+ caracteres)", gateway_configurado()),
                ("Gateway ligado (scripts\\gateway_whatsapp.ps1)", bool(situacao)),
                ("Celular conectado pelo QR code", situacao.get("estado") == "conectado"),
                ("IA (Claude) ligada (ANTHROPIC_API_KEY)", ia.ia_disponivel()),
            ]
        else:
            itens = [
                ("Atendimento ligado (Configuração do portal)", ConfiguracaoPortal.atual().whatsapp_ativo),
                ("Token e número da Meta (WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID)", meta_configurada()),
                ("App Secret para conferir a assinatura (WHATSAPP_APP_SECRET)", bool(settings.WHATSAPP_APP_SECRET)),
                ("Senha do webhook (WHATSAPP_VERIFY_TOKEN)", bool(settings.WHATSAPP_VERIFY_TOKEN)),
                ("Modelo aprovado para avisos fora das 24 h (WHATSAPP_MODELO_AVISO)",
                 bool(settings.WHATSAPP_MODELO_AVISO)),
                ("IA (Claude) ligada (ANTHROPIC_API_KEY)", ia.ia_disponivel()),
            ]
        return TemplateResponse(request, "admin/whatsapp/conexao.html", self._contexto(
            request, title="Conexão do WhatsApp", itens=itens, ultima=ultima, modo_gateway=modo_gateway,
            situacao=situacao, erro_gateway=erro_gateway,
            numero_conectado=mascarar(situacao.get("numero") or ""),
            webhook=settings.PORTAL_URL.rstrip("/") + reverse("whatsapp:webhook"),
            celulares=Funcionario.objects.filter(situacao=Funcionario.Situacao.ATIVO).exclude(whatsapp="").count(),
            ativos=Funcionario.objects.filter(situacao=Funcionario.Situacao.ATIVO).count(),
            conversaram=Conversa.objects.filter(simulada=False, funcionario__isnull=False).count(),
            agora=timezone.now(),
        ))


@admin.register(PerguntaSemResposta)
class PerguntaSemRespostaAdmin(admin.ModelAdmin):
    list_display = ("texto", "funcionario", "criada_em", "resolvida")
    list_editable = ("resolvida",)
    list_filter = ("resolvida", "criada_em")
    search_fields = ("texto",)
    readonly_fields = ("texto", "funcionario", "conversa", "criada_em")

    def has_add_permission(self, request):
        return False


@admin.register(PedidoCadastro)
class PedidoCadastroAdmin(admin.ModelAdmin):
    """Números fora do cadastro que pediram acesso pelo WhatsApp: o RH confere e libera (ou recusa)."""

    list_display = ("nome_informado", "cpf", "celular", "situacao", "criado_em", "funcionario")
    list_filter = ("situacao",)
    search_fields = ("nome_informado", "matricula_informada", "numero")
    list_select_related = ("funcionario",)

    @admin.display(description="celular", ordering="numero")
    def celular(self, obj):
        return formatar_celular(obj.numero)

    @admin.display(description="CPF informado")
    def cpf(self, obj):
        return obj.cpf_mascarado or "— ainda não informou"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def get_urls(self):
        return [path("<int:pk>/analisar/", self.admin_site.admin_view(self.analisar_view),
                     name="whatsapp_pedidocadastro_analisar")] + super().get_urls()

    def change_view(self, request, object_id, form_url="", extra_context=None):
        return redirect("admin:whatsapp_pedidocadastro_analisar", object_id)

    def pode_resolver(self, request):
        return request.user.has_perm("contas.change_funcionario") and request.user.has_perm("whatsapp.atender_whatsapp")

    def analisar_view(self, request, pk):
        if not self.has_view_permission(request):
            raise PermissionDenied
        pedido = get_object_or_404(PedidoCadastro.objects.select_related("conversa", "funcionario"), pk=pk)
        if request.method == "POST":
            if not self.pode_resolver(request):
                raise PermissionDenied
            try:
                if "recusar" in request.POST:
                    avisado = recusar(pedido, request.user)
                    messages.info(request, "Pedido recusado." + ("" if avisado else " (O aviso no WhatsApp não saiu.)"))
                else:
                    funcionario = get_object_or_404(Funcionario, pk=request.POST.get("funcionario"))
                    avisado = liberar(pedido, funcionario, request.user, request=request)
                    messages.success(request, f"Liberado: o celular agora está no cadastro de {funcionario.nome_completo}."
                                     + (" A pessoa foi avisada no WhatsApp." if avisado else
                                        " (O aviso no WhatsApp não saiu: o gateway está conectado?)"))
            except ErroCadastro as erro:
                messages.error(request, str(erro))
                return redirect("admin:whatsapp_pedidocadastro_analisar", pk)
            return redirect("admin:whatsapp_pedidocadastro_changelist")

        busca = request.GET.get("busca", "").strip()
        candidatos = []
        if pedido.cpf_informado:  # o dono do CPF vem primeiro: é o único que pode ser liberado
            candidatos += list(Funcionario.objects.filter(cpf=pedido.cpf_informado).select_related("contrato"))
        if pedido.matricula_informada:
            candidatos += [f for f in Funcionario.objects.filter(matricula=pedido.matricula_informada)
                           .select_related("contrato") if f not in candidatos]
        candidatos += [f for f in buscar_funcionarios(busca or pedido.nome_informado) if f not in candidatos]
        if not busca and not candidatos:  # nome escrito diferente: tenta com o primeiro e o último nome
            partes = pedido.nome_informado.split()
            candidatos = list(buscar_funcionarios(f"{partes[0]} {partes[-1]}"))
        celular_novo = normalizar_celular(pedido.numero)
        linhas = [{"f": f, "nome_confere": nomes_compativeis(pedido.nome_informado, f.nome_completo),
                   "matricula_confere": bool(pedido.matricula_informada) and f.matricula == pedido.matricula_informada,
                   "cpf_confere": bool(pedido.cpf_informado) and f.cpf == pedido.cpf_informado,
                   "troca_celular": bool(f.whatsapp) and f.whatsapp != celular_novo}
                  for f in candidatos]
        novo = (f"{reverse('admin:contas_funcionario_add')}?nome_completo={quote(pedido.nome_informado)}"
                f"&telefone={quote(formatar_celular(pedido.numero))}&matricula={quote(pedido.matricula_informada)}"
                f"&cpf={quote(pedido.cpf_informado)}")
        return TemplateResponse(request, "admin/whatsapp/pedido_cadastro.html", self._contexto(
            request, pedido, title=f"Conferir cadastro: {pedido.nome_informado}", linhas=linhas, busca=busca,
            celular=formatar_celular(pedido.numero), link_novo=novo, pode_resolver=self.pode_resolver(request),
            mensagens=pedido.conversa.mensagens.order_by("-criada_em")[:10][::-1],
        ))

    def _contexto(self, request, pedido, **extra):
        return {**self.admin_site.each_context(request), "opts": self.model._meta, "pedido": pedido, **extra}
