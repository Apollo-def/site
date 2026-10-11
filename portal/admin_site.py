"""
Painel do RH: o "site" administrativo com o menu lateral organizado por tarefa (e não por módulo do sistema).
Cada pessoa só vê no menu o que a permissão dela deixa abrir. Os números ao lado dos itens mostram o que
está esperando o RH.
"""
from django.contrib import admin
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

# Ícones (traço, 24x24) no estilo dos ícones de sistema. Só o miolo do <svg>.
ICONES = {
    "inicio": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M10 21v-6h4v6"/>',
    "grafico": '<path d="M3 3v18h18"/><path d="M7 15l4-4 3 3 5-6"/>',
    "conversa": '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/>',
    "pergunta": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.2a2.6 2.6 0 0 1 5 .9c0 1.8-2.5 2.4-2.5 4"/><path d="M12 17.5h.01"/>',
    "simulador": '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
    "conexao": '<path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4z"/><path d="M14 14h2v2h-2zM18 18h2v2h-2zM18 14h2M14 18v2"/>',
    "enviar": '<path d="M12 16V4"/><path d="M7 9l5-5 5 5"/><path d="M4 16v4h16v-4"/>',
    "contracheque": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>'
                    '<path d="M14.5 12.5h-3a1.5 1.5 0 0 0 0 3h1a1.5 1.5 0 0 1 0 3h-3"/>',
    "alerta": '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>'
              '<path d="M12 9v4M12 17h.01"/>',
    "documento": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h8"/>',
    "pedido": '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 3v2h6V3"/><path d="M9 13l2 2 4-4"/>',
    "tipos": '<path d="M4 6h16M4 12h16M4 18h10"/>',
    "camisa": '<path d="M8 3 3 6l2 4 2-1v12h10V9l2 1 2-4-5-3-2 2h-2z"/>',
    "calendario": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "pessoas": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/><path d="M16 4.5a3.5 3.5 0 0 1 0 7"/>'
               '<path d="M18 14.5a6.5 6.5 0 0 1 3.5 5.5"/>',
    "predio": '<path d="M4 21V5l8-3v19"/><path d="M12 9h8v12"/><path d="M8 8h.01M8 12h.01M8 16h.01M16 13h.01M16 17h.01"/>',
    "livro": '<path d="M2 4h6a4 4 0 0 1 4 4v13a3 3 0 0 0-3-3H2z"/><path d="M22 4h-6a4 4 0 0 0-4 4v13a3 3 0 0 1 3-3h7z"/>',
    "pasta": '<path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "clipe": '<path d="M21 11.5 12.5 20a5 5 0 0 1-7-7L14 4.5a3.3 3.3 0 0 1 4.7 4.7L10 18a1.7 1.7 0 0 1-2.3-2.3l8-8"/>',
    "engrenagem": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 '
                  '1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1'
                  'a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1'
                  'a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 '
                  '1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    "novo_usuario": '<circle cx="10" cy="8" r="4"/><path d="M3 21a7 7 0 0 1 14 0"/><path d="M19 8v6M16 11h6"/>',
    "usuario": '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "cadeado": '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "lista": '<path d="M9 6h12M9 12h12M9 18h12M4 6h.01M4 12h.01M4 18h.01"/>',
}

# (seção, [(rótulo, nome da URL, permissão exigida, ícone, contador)]). Permissão None = só superusuário.
MENU = [
    ("", [
        ("Visão geral", "admin:index", "", "inicio", None),
        ("Acompanhamento", "painel_acompanhamento", "contas.view_funcionario", "grafico", None),
    ]),
    ("WhatsApp", [
        ("Atendimentos", "admin:whatsapp_conversa_changelist", "whatsapp.view_conversa", "conversa", "com_rh"),
        ("Cadastros pendentes", "admin:whatsapp_pedidocadastro_changelist", "whatsapp.view_pedidocadastro",
         "novo_usuario", "cadastros"),
        ("Perguntas sem resposta", "admin:whatsapp_perguntasemresposta_changelist",
         "whatsapp.view_perguntasemresposta", "pergunta", "perguntas"),
        ("Simulador", "admin:whatsapp_simulador", "whatsapp.atender_whatsapp", "simulador", None),
        ("Conexão (QR code)", "admin:whatsapp_conexao", None, "conexao", None),
    ]),
    ("Documentos", [
        ("Enviar contracheques", "admin:contracheques_lotecontracheque_changelist",
         "contracheques.view_lotecontracheque", "enviar", None),
        ("Contracheques", "admin:contracheques_contracheque_changelist", "contracheques.view_contracheque",
         "contracheque", None),
        ("Páginas sem dono", "admin:contracheques_paginanaoidentificada_changelist",
         "contracheques.view_paginanaoidentificada", "alerta", "paginas"),
        ("Enviar termos e registros", "admin:documentos_lotedocumento_changelist", "documentos.view_lotedocumento",
         "enviar", None),
        ("Documentos dos funcionários", "admin:documentos_documentofuncionario_changelist",
         "documentos.view_documentofuncionario", "documento", None),
    ]),
    ("Pedidos", [
        ("Solicitações", "admin:solicitacoes_solicitacao_changelist", "solicitacoes.view_solicitacao", "pedido",
         "pedidos"),
        ("Faltas e coberturas", "admin:ocorrencias_ocorrencia_changelist", "ocorrencias.view_ocorrencia",
         "calendario", "ocorrencias"),
        ("Fardamento (relatório)", "admin:solicitacoes_itemfardamento_changelist",
         "solicitacoes.view_itemfardamento", "camisa", None),
        ("Tipos de solicitação", "admin:solicitacoes_tiposolicitacao_changelist",
         "solicitacoes.view_tiposolicitacao", "tipos", None),
    ]),
    ("Cadastro", [
        ("Funcionários", "admin:contas_funcionario_changelist", "contas.view_funcionario", "pessoas", None),
        ("Contratos / lotações", "admin:contas_contrato_changelist", "contas.view_contrato", "predio", None),
    ]),
    ("Central de orientações", [
        ("Orientações", "admin:orientacoes_orientacao_changelist", "orientacoes.view_orientacao", "livro", None),
        ("Categorias", "admin:orientacoes_categoria_changelist", "orientacoes.view_categoria", "pasta", None),
        ("Anexos", "admin:orientacoes_anexo_changelist", "orientacoes.view_anexo", "clipe", None),
    ]),
    ("Sistema", [
        ("Configuração", "admin:contas_configuracaoportal_changelist", "contas.view_configuracaoportal",
         "engrenagem", None),
        ("Usuários do RH", "admin:contas_usuario_changelist", "contas.view_usuario", "usuario", None),
        ("Verificação em 2 etapas", "admin:duas_etapas_configurar", "", "cadeado", None),
        ("Grupos de permissão", "admin:auth_group_changelist", "auth.view_group", "cadeado", None),
        ("Auditoria", "admin:auditoria_logauditoria_changelist", "auditoria.view_logauditoria", "lista", None),
    ]),
]


def contadores():
    """O que está esperando o RH (aparece como número ao lado do item do menu)."""
    from contracheques.models import PaginaNaoIdentificada
    from ocorrencias.models import Ocorrencia
    from solicitacoes.models import Solicitacao
    from whatsapp.models import Conversa, PedidoCadastro, PerguntaSemResposta

    return {
        "com_rh": Conversa.objects.filter(estado=Conversa.Estado.HUMANO, simulada=False).count(),
        "perguntas": PerguntaSemResposta.objects.filter(resolvida=False).count(),
        "cadastros": PedidoCadastro.objects.filter(situacao=PedidoCadastro.Situacao.PENDENTE).count(),
        "paginas": PaginaNaoIdentificada.objects.filter(resolvida=False).count(),
        "pedidos": Solicitacao.objects.pendentes().count(),
        "ocorrencias": Ocorrencia.objects.filter(situacao=Ocorrencia.Situacao.CONFERIR).count(),
    }


def montar_menu(request):
    usuario = request.user
    numeros = contadores()
    secoes, melhor = [], None
    for titulo, itens in MENU:
        visiveis = []
        for rotulo, nome_url, permissao, icone, contador in itens:
            if permissao is None and not usuario.is_superuser:
                continue
            if permissao and not usuario.has_perm(permissao):
                continue
            try:
                url = reverse(nome_url)
            except NoReverseMatch:
                continue
            item = {"rotulo": rotulo, "url": url, "icone": ICONES[icone], "numero": numeros.get(contador) or 0,
                    "ativo": False}
            visiveis.append(item)
            # A página atual acende o item com o endereço mais longo que combina (a Visão geral só na própria).
            combina = request.path == url or (url != reverse("admin:index") and request.path.startswith(url))
            if combina and (melhor is None or len(url) > len(melhor["url"])):
                melhor = item
        if visiveis:
            secoes.append({"titulo": titulo, "itens": visiveis})
    if melhor:
        melhor["ativo"] = True
    return secoes


def resumo_da_visao_geral():
    """Números da tela inicial do painel."""
    from contas.models import ConfiguracaoPortal, Funcionario
    from whatsapp.models import Conversa, Mensagem

    hoje = timezone.localdate()
    ativos = Funcionario.objects.filter(situacao=Funcionario.Situacao.ATIVO)
    total = ativos.count()
    com_celular = ativos.exclude(whatsapp="").count()
    return {
        "total_ativos": total,
        "com_celular": com_celular,
        "sem_celular": total - com_celular,
        "pct_com_celular": round(100 * com_celular / total) if total else 0,
        "mensagens_hoje": Mensagem.objects.filter(direcao=Mensagem.Direcao.ENTRADA, conversa__simulada=False,
                                                  criada_em__date=hoje).count(),
        "conversas_total": Conversa.objects.filter(simulada=False, funcionario__isnull=False).count(),
        "whatsapp_ligado": ConfiguracaoPortal.atual().whatsapp_ativo,
    }


class PainelAdmin(admin.AdminSite):
    site_header = "Painel do RH"
    site_title = "Painel do RH — Grupo Global"
    index_title = "Visão geral"
    site_url = None  # o site público é só a página do WhatsApp: sem o link "Ver o site"
    # O menu lateral é o nosso: templates/admin/base_site.html troca o conteúdo do bloco "nav-sidebar".

    def each_context(self, request):
        contexto = super().each_context(request)
        if request.user.is_active and request.user.is_staff:
            contexto["menu_lateral"] = montar_menu(request)
            contexto["duas_etapas_pendente"] = not getattr(request.user, "duas_etapas_ativa", True)
            aviso = request.session.get("senha_fraca")
            if aviso and aviso.get("senha") == request.user.password[-16:]:
                contexto["senha_fraca"] = aviso["problemas"]
            elif aviso:
                request.session.pop("senha_fraca")  # a senha já foi trocada
        return contexto

    def get_urls(self):
        from django.urls import path

        from contas import views_duas_etapas as telas
        extras = [
            path("seguranca/codigo/", self.admin_view(lambda r: telas.verificar(self, r)), name="duas_etapas_verificar"),
            path("seguranca/duas-etapas/", self.admin_view(lambda r: telas.configurar(self, r)),
                 name="duas_etapas_configurar"),
        ]
        return extras + super().get_urls()

    def index(self, request, extra_context=None):
        return super().index(request, {**resumo_da_visao_geral(), **contadores(), **(extra_context or {})})
