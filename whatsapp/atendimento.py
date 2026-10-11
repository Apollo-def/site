"""
O atendimento do Assistente do Colaborador: recebe cada mensagem, identifica o funcionário pelo celular
cadastrado pelo RH e entrega o que foi pedido. O funcionário não usa login nem senha:
- só envia documento para o celular que está no cadastro do próprio funcionário (número repetido no
  cadastro não recebe nada);
- desligado sem permissão de acesso não recebe nada;
- tudo vai para a auditoria.
"""
import re
from datetime import date, timedelta

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.signing import TimestampSigner
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from auditoria.models import LogAuditoria
from auditoria.servicos import registrar
from contas.models import ConfiguracaoPortal, Funcionario
from contas.validadores import cpf_e_valido, somente_digitos
from contracheques.models import MESES, Contracheque, TipoFolha
from documentos.models import TipoDocumento
from orientacoes.models import Orientacao
from solicitacoes.models import ItemFardamento, Solicitacao
from solicitacoes.servicos import (TAMANHO_MAXIMO_ANEXO_MB, criar_solicitacao_fardamento, criar_solicitacao_vt,
                                   extensao_pelo_conteudo, fardamento_em_andamento, termo_vt_do_funcionario,
                                   tipo_fardamento_disponivel, tipos_vt_disponiveis, vt_em_andamento)

from . import ia
from .cadastro import matricula_informada, nome_valido
from .canal import ErroCanal, canal_para
from .interpretacao import Acao, Pedido, _tem, interpretar, normalizar, opcao_vt
from .models import Conversa, Mensagem, PedidoCadastro, PerguntaSemResposta, mascarar_cpf
from .numeros import variantes

Estado = Conversa.Estado
ESCOLHA_NUMERADA = re.compile(r"\s*\d{1,2}(?:\s*(?:,|;|e|\s)\s*\d{1,2})*\s*[.!]?\s*", re.IGNORECASE)
LIMITE_MENSAGENS_POR_MINUTO = 12  # por número: acima disso o assistente para de responder até passar o minuto
TAMANHO_MAXIMO_TEXTO = 4000
SALT_LINK = "whatsapp.link-documento"
HORAS_LINK = 24
ASSUNTOS_SENSIVEIS = ("demissao", "demitid", "rescisao", "acidente", "assedio", "processo", "advogado", "justica",
                      "denuncia", "desconto indevido", "descontaram errado", "cobrado errado", "salario errado",
                      "pagaram errado", "nao recebi", "atestado")
DUVIDA_ABERTA = "duvida_aberta"  # opção "tirar uma dúvida": só pede para a pessoa escrever
# Quando o assistente não entende, mas dá para adivinhar o assunto pela palavra: orienta em vez de só mostrar o menu.
DICAS = [
    (("salario", "pagamento", "pago", "caiu", "cair", "deposito", "cade", "dinheiro"),
     "Se é sobre o seu pagamento: o contracheque mostra os valores do mês (responda *1*). Para saber a data do "
     "pagamento ou de um valor que não caiu, responda *{rh}* e uma pessoa do RH te ajuda."),
    (("va ", "alimentacao", "ticket", "cesta", "refeicao"),
     "Se é sobre o vale-alimentação, {duvida}responda *{rh}* para falar com uma pessoa do RH."),
]
# O que chega e o assistente não consegue ler: fica registrado assim na conversa e ele responde pedindo texto.
REGISTRO_DO_TIPO = {"audio": "[áudio]", "video": "[vídeo]", "ligacao": "[ligação recusada]", "figurinha": "[figurinha]"}
AVISO_DO_TIPO = {
    "audio": "🎤 Ainda não consigo ouvir áudios. Pode escrever a sua mensagem, por favor?",
    "video": "🎥 Não consigo ver vídeos. Pode escrever a sua mensagem? Para mandar um documento, envie uma foto ou PDF.",
    "ligacao": "📞 Não consigo atender ligações por aqui. Escreva a sua mensagem que eu te ajudo.",
}
# Número fora do cadastro: antes de pedir nome e CPF, pergunta quem é (quem procura vaga não vira cadastro pendente).
MENU_PERFIL = ("Olá! Sou o Assistente do Colaborador do Grupo Global. Para eu te ajudar, você é:\n"
               "1 - Funcionário do Grupo Global\n"
               "2 - Quero enviar currículo / saber de vagas\n"
               "3 - Outro assunto\n\nResponda com o número.")
PALAVRAS_DE_VAGA = ("curriculo", "vaga", "emprego", "trabalhar com voces", "trabalhar com vcs", "trabalhar ai",
                    "trabalhar na empresa", "oportunidade", "selecao", "entrevista", "contratando", "candidat",
                    "admissao", "procurando trabalho")
PALAVRAS_DE_FUNCIONARIO = ("contracheque", "holerite", "vale transporte", "termo", "registro", "funcionari",
                           "fardamento", "farda", "uniforme", "colaborador", "trabalho no", "trabalho na", "trabalho pra", "sou do grupo")
TEXTO_CURRICULO_PADRAO = ("Que bom que você quer trabalhar com a gente! 😊\n\nEste número é o atendimento dos "
                          "funcionários do Grupo Global e não recebe currículos. Para se candidatar, procure o RH "
                          "pelos canais oficiais da empresa.")
HORAS_PARA_VOLTAR_AO_ASSISTENTE = 24
LIGACAO_COM_O_RH = "📞 Não dá para atender ligações por aqui. Pode escrever, que o RH te responde nesta conversa."
PASTA_AUDIOS ="whatsapp/recebidos/audios/"  # apagados junto com as mensagens antigas (limpar_whatsapp)
PEDIR_CPF = ("Agora me mande o seu CPF (só os números). Ele serve para o RH ter certeza de que é você antes de "
             "liberar os seus documentos.")
ANOTADO_PARA_O_RH = ("Anotei a sua mensagem e ela fica aqui para o RH ler. Assim que liberarem o seu cadastro, "
                     "eu te aviso.")
ENCERRAMENTOS = ("Por nada! Se precisar, é só chamar. 😊", "👍 Combinado! Quando precisar, é só me chamar.")
MENUS = {
    "principal": [
        ("Contracheque", Pedido(Acao.CONTRACHEQUE)),
        ("Termo de vale-transporte (utilizar ou cancelar)", Pedido(Acao.VT)),
        ("Registro de empregado", Pedido(Acao.REGISTRO)),
        ("Fardamento (camisa, calça, bota...)", Pedido(Acao.FARDAMENTO)),
        ("Andamento do seu pedido", Pedido(Acao.STATUS)),
        ("Tirar uma dúvida (VT, VA, férias, documentos...)", DUVIDA_ABERTA),
        ("Falar com uma pessoa do RH", Pedido(Acao.ATENDENTE)),
    ],
    "vt": [
        ("Quero UTILIZAR o vale-transporte", Pedido(Acao.TERMO_VT, opcao_vt="vt_utilizar")),
        ("Quero CANCELAR o vale-transporte", Pedido(Acao.TERMO_VT, opcao_vt="vt_cancelar")),
        ("Ver o andamento do meu pedido de VT", Pedido(Acao.STATUS)),
        ("Tirar uma dúvida sobre o vale-transporte", DUVIDA_ABERTA),
    ],
}
OPCAO_DE_MENU = re.compile(r"\s*(\d)\s*[.)-]?\s*")
PALAVRAS_POUCO_UTEIS = {"como", "quando", "qual", "quais", "onde", "para", "pra", "pro", "por", "que", "com", "sem",
                        "uma", "uns", "meu", "minha", "esta", "isso", "voce", "voces", "tem", "ter", "ser", "sobre",
                        "fazer", "posso", "pode", "quero", "preciso", "saber", "duvida", "ola", "bom", "dia", "boa"}


# ---------------------------------------------------------------------------
# Entrada
# ---------------------------------------------------------------------------
def receber(numero, texto="", midia=None, simulada=False, id_externo="", tipo=""):
    """
    Ponto de entrada (webhook da Meta, gateway ou simulador do painel).
    midia: {"conteudo": bytes, "nome": str} para foto/PDF recebido.
    tipo: "audio", "video", "ligacao" ou "figurinha" (o que o assistente não consegue ler).
    """
    texto = (texto or "")[:TAMANHO_MAXIMO_TEXTO]
    if tipo == "rh_celular":
        return registrar_mensagem_do_celular(numero, texto, id_externo)
    if not texto.strip() and not midia and tipo not in REGISTRO_DO_TIPO:
        return None  # sinal interno do WhatsApp, sem conteúdo: responder seria repetir a última resposta
    with transaction.atomic():
        conversa, _ = Conversa.objects.select_for_update().get_or_create(numero=numero, simulada=simulada)
        if id_externo and conversa.mensagens.filter(id_externo=id_externo).exists():
            return conversa  # a Meta reenviou a mesma mensagem
        Mensagem.objects.create(
            conversa=conversa, direcao=Mensagem.Direcao.ENTRADA, autor=Mensagem.Autor.FUNCIONARIO,
            texto=esconder_cpf(texto) or REGISTRO_DO_TIPO.get(tipo, ""), anexo_nome=(midia or {}).get("nome", "")[:150],
            id_externo=id_externo[:128],
        )
        conversa.ultima_entrada_em = timezone.now()
        conversa.save(update_fields=["ultima_entrada_em", "atualizada_em"])
        recentes = conversa.mensagens.filter(direcao=Mensagem.Direcao.ENTRADA,
                                             criada_em__gte=timezone.now() - timedelta(minutes=1)).count()
    atendimento = Atendimento(conversa)
    # Alguém disparando mensagens: guarda, mas não responde. Na conversa com o RH não vale: lá é normal mandar
    # várias mensagens curtas seguidas, e o aviso do robô atrapalharia.
    if recentes > LIMITE_MENSAGENS_POR_MINUTO and conversa.estado != Conversa.Estado.HUMANO:
        if recentes == LIMITE_MENSAGENS_POR_MINUTO + 1:
            atendimento.responder("Recebi muitas mensagens seguidas. Aguarde um minuto e me escreva de novo, por favor.")
        return conversa
    if tipo == "figurinha":
        texto = "👍"  # figurinha vale como um emoji: "ok"
    atendimento.processar(texto, midia, tipo)
    return conversa


def registrar_mensagem_do_celular(numero, texto, id_externo=""):
    """
    Alguém do RH escreveu direto pelo celular do chip (não pelo painel). A mensagem entra no histórico e a
    conversa passa para o RH: o assistente para de responder nela, para não entrar no meio da conversa.
    Volta sozinha para o assistente depois de 24 h parada (Atendimento.rh_ja_encerrou) ou pelo botão no painel.
    """
    with transaction.atomic():
        conversa, _ = Conversa.objects.select_for_update().get_or_create(numero=numero, simulada=False)
        if id_externo and conversa.mensagens.filter(id_externo=id_externo).exists():
            return conversa  # é uma mensagem que o próprio sistema mandou
        Mensagem.objects.create(conversa=conversa, direcao=Mensagem.Direcao.SAIDA, autor=Mensagem.Autor.RH,
                                texto=esconder_cpf(texto), id_externo=id_externo[:128])
        conversa.funcionario = conversa.funcionario or identificar(numero)
        conversa.estado, conversa.contexto = Conversa.Estado.HUMANO, {}
        conversa.save(update_fields=["funcionario", "estado", "contexto", "atualizada_em"])
    return conversa


class Atendimento:
    def __init__(self, conversa):
        self.conversa = conversa
        self.config = ConfiguracaoPortal.atual()
        self.canal = canal_para(conversa)
        self.funcionario = None

    # -----------------------------------------------------------------
    # Envio
    # -----------------------------------------------------------------
    def responder(self, texto, autor=Mensagem.Autor.ASSISTENTE):
        mensagem = Mensagem(conversa=self.conversa, direcao=Mensagem.Direcao.SAIDA, autor=autor, texto=texto)
        try:
            mensagem.id_externo = self.canal.enviar_texto(self.conversa, texto)
        except ErroCanal:
            mensagem.falhou = True
        mensagem.save()

    def enviar_arquivo(self, arquivo, nome_arquivo, legenda, objeto):
        """Envia um documento já entregue ao funcionário (PDF na conversa ou link de 24 h)."""
        mensagem = Mensagem(conversa=self.conversa, direcao=Mensagem.Direcao.SAIDA, autor=Mensagem.Autor.ASSISTENTE,
                            anexo_nome=nome_arquivo, anexo_caminho=arquivo.name)
        try:
            if self.config.whatsapp_forma_envio == ConfiguracaoPortal.FormaEnvio.LINK:
                mensagem.texto = f"{legenda}\n{link_seguro(objeto, self.funcionario)}\n(o link vale por {HORAS_LINK} horas)"
                mensagem.id_externo = self.canal.enviar_texto(self.conversa, mensagem.texto)
            else:
                mensagem.texto = legenda
                with arquivo.open("rb") as entrada:
                    conteudo = entrada.read()
                mensagem.id_externo = self.canal.enviar_documento(self.conversa, conteudo, nome_arquivo, legenda)
        except ErroCanal:
            mensagem.falhou = True
        mensagem.save()
        if not mensagem.falhou:
            registrar(LogAuditoria.Acao.WHATSAPP_DOCUMENTO_ENVIADO, usuario=self.funcionario.usuario, objeto=objeto,
                      identificador=self.funcionario.matricula, descricao=f"{nome_arquivo} ({self.conversa_descricao})")
        return not mensagem.falhou

    @property
    def conversa_descricao(self):
        return "simulador do painel" if self.conversa.simulada else "WhatsApp"

    def mudar(self, estado, **contexto):
        self.conversa.estado = estado
        self.conversa.contexto = contexto
        self.conversa.save(update_fields=["estado", "contexto", "funcionario", "atualizada_em",
                                          "aguardando_humano_desde"])

    # -----------------------------------------------------------------
    # Fluxo principal
    # -----------------------------------------------------------------
    def processar(self, texto, midia, tipo=""):
        conversa = self.conversa
        if conversa.estado == Estado.HUMANO and self.rh_ja_encerrou():
            conversa.aguardando_humano_desde = None  # o RH respondeu e a conversa parou: volta para o assistente
            self.mudar(Estado.ATENDIMENTO)
        if conversa.estado == Estado.HUMANO:
            # Com uma pessoa do RH o assistente não fala nada: a mensagem (e o áudio) fica para o RH no painel.
            if tipo == "audio":
                return self.receber_audio(midia)
            if tipo == "ligacao":
                return self.responder_uma_vez(LIGACAO_COM_O_RH)
            return None
        if not self.config.whatsapp_ativo and not conversa.simulada:
            return self.responder("O atendimento pelo WhatsApp está desligado no momento. Procure o RH.")
        if tipo == "audio":
            return self.receber_audio(midia)
        if tipo == "ligacao":  # cada ligação é uma tentativa de verdade: sempre explica
            return self.responder(AVISO_DO_TIPO[tipo])
        if tipo in AVISO_DO_TIPO:  # vídeo
            return self.responder_uma_vez(AVISO_DO_TIPO[tipo])

        self.funcionario = identificar(conversa.numero)
        if self.funcionario is None:
            return self.numero_fora_do_cadastro(texto, midia)
        PedidoCadastro.objects.filter(numero=conversa.numero, situacao=PedidoCadastro.Situacao.PENDENTE).update(
            situacao=PedidoCadastro.Situacao.LIBERADO, funcionario=self.funcionario, resolvido_em=timezone.now())
        if not self.funcionario.pode_acessar:
            return self.responder("Seu acesso ao atendimento está desativado. Procure o RH.")
        if conversa.funcionario_id != self.funcionario.pk or conversa.estado == Estado.NOVA:
            return self.boas_vindas(texto, midia)
        self.atender(texto, midia)

    def numero_fora_do_cadastro(self, texto, midia=None):
        """
        Pede nome e CPF e manda para o RH conferir. Não entrega nada antes de o RH liberar, e o RH só consegue
        liberar para o funcionário que tem este CPF. O assistente nunca diz se o CPF existe no cadastro.
        """
        conversa = self.conversa
        conversa.funcionario = None
        pedidos = PedidoCadastro.objects.filter(numero=conversa.numero).order_by("-criado_em")
        pendente = pedidos.filter(situacao=PedidoCadastro.Situacao.PENDENTE).first()
        if pendente and not pendente.cpf_informado:  # pedido de antes da conferência por CPF: pede o que falta
            if conversa.estado != Estado.AGUARDANDO_CPF:
                self.mudar(Estado.AGUARDANDO_CPF, pedido=pendente.pk)
                return self.responder(PEDIR_CPF)
            cpf = self._cpf_valido(texto)
            if cpf:
                PedidoCadastro.objects.filter(pk=pendente.pk).update(cpf_informado=cpf)
                self.mudar(Estado.NOVA)
                return self.responder("Obrigado! O RH vai conferir os seus dados. Assim que liberarem, eu te aviso aqui.")
            return None
        if pendente:
            # Responde uma vez e, se a pessoa continuar escrevendo, avisa que anotou e depois fica quieto
            # (a mensagem fica na conversa para o RH ler). Antes repetia a mesma frase a cada mensagem.
            ultima = self.ultima_resposta()
            if ultima and ultima.texto == ANOTADO_PARA_O_RH:
                return
            if ultima and ultima.texto.startswith(("Seus dados (", "Pronto! Enviei os seus dados")):
                return self.responder(ANOTADO_PARA_O_RH)
            return self.responder(f"Seus dados ({pendente.nome_informado}) já estão com o RH para conferência. "
                                  "Assim que liberarem, eu te aviso aqui.")
        if conversa.estado == Estado.AGUARDANDO_NOME:
            nome = nome_valido(texto)
            if not nome:
                return self.responder("Escreva o seu nome completo, por favor (nome e sobrenome).")
            self.mudar(Estado.AGUARDANDO_CPF, nome=nome)
            return self.responder(f"Obrigado! {PEDIR_CPF}")
        if conversa.estado == Estado.AGUARDANDO_MATRICULA and conversa.contexto.get("nome"):
            # Conversa que começou antes de o CPF ser pedido: guarda a matrícula e pede o CPF.
            self.mudar(Estado.AGUARDANDO_CPF, nome=conversa.contexto["nome"], matricula=matricula_informada(texto))
            return self.responder(f"Obrigado! {PEDIR_CPF}")
        if conversa.estado == Estado.AGUARDANDO_CPF and conversa.contexto.get("nome"):
            cpf = self._cpf_valido(texto)
            if not cpf:
                return None
            PedidoCadastro.objects.create(conversa=conversa, numero=conversa.numero,
                                          nome_informado=conversa.contexto["nome"], cpf_informado=cpf,
                                          matricula_informada=conversa.contexto.get("matricula", ""))
            self.mudar(Estado.NOVA)
            return self.responder("Pronto! Enviei os seus dados para o RH conferir. Por segurança, os documentos só "
                                  "são liberados depois dessa conferência. Assim que liberarem, eu te aviso aqui.")
        recusado = pedidos.filter(situacao=PedidoCadastro.Situacao.RECUSADO).first()
        if recusado and recusado.resolvido_em and recusado.resolvido_em > timezone.now() - timedelta(days=30):
            self.mudar(Estado.NOVA)
            return self.responder("Não encontrei este número no cadastro de funcionários. Procure o seu supervisor "
                                  "ou o RH, por favor.")
        if quer_vaga(texto) or midia:  # já chegou falando de vaga, ou mandando o currículo em PDF/foto
            return self.responder_sobre_vagas()
        if conversa.estado == Estado.AGUARDANDO_PERFIL:
            return self.escolher_perfil(texto)
        if _tem(normalizar(texto), PALAVRAS_DE_FUNCIONARIO):  # "quero meu contracheque": é funcionário
            self.mudar(Estado.AGUARDANDO_NOME)
            return self.responder("Olá! Sou o Assistente do Colaborador do Grupo Global. Ainda não encontrei este "
                                  "número no cadastro de funcionários. Para liberar o seu atendimento, me diga o seu "
                                  "nome completo.")
        self.mudar(Estado.AGUARDANDO_PERFIL)
        self.responder(MENU_PERFIL)

    def escolher_perfil(self, texto):
        """Resposta ao "você é: 1 funcionário / 2 vaga / 3 outro assunto" de quem não está no cadastro."""
        t = normalizar(texto)
        if t.strip() == "1" or _tem(t, PALAVRAS_DE_FUNCIONARIO):
            self.mudar(Estado.AGUARDANDO_NOME)
            return self.responder("Certo! Para liberar o seu atendimento, me diga o seu nome completo.")
        if t.strip() == "2":
            return self.responder_sobre_vagas()
        if t.strip() == "3" or _tem(t, ("outro", "rh", "pessoa", "atendente")):
            self.conversa.aguardando_humano_desde = timezone.now()
            self.mudar(Estado.HUMANO)
            return self.responder("Certo! Escreva aqui o seu assunto que uma pessoa do RH te responde por esta "
                                  f"conversa, {self.config.whatsapp_horario_atendimento}.")
        self.responder_uma_vez("Para eu te ajudar, responda com o número: *1* (sou funcionário), *2* (currículo / "
                               "vagas) ou *3* (outro assunto).")

    def responder_sobre_vagas(self):
        """Quem procura emprego não vira "cadastro pendente": recebe o caminho do currículo (texto da Configuração)."""
        self.mudar(Estado.AGUARDANDO_PERFIL)
        self.responder_uma_vez(self.config.whatsapp_texto_curriculo.strip() or TEXTO_CURRICULO_PADRAO)

    def _cpf_valido(self, texto):
        """O CPF digitado (só números) se os dígitos conferem; senão pede de novo e devolve ''."""
        cpf = somente_digitos(texto)
        if cpf_e_valido(cpf):
            return cpf
        if "sei" in normalizar(texto) or "nao" in normalizar(texto):
            self.responder("Preciso do CPF para o RH ter certeza de que é você. Ele está no seu RG, na carteira de "
                           "trabalho ou no aplicativo gov.br. Mande só os 11 números, por favor.")
        else:
            self.responder("Esse CPF não parece certo. Confira e mande os 11 números, por favor (ex.: 12345678909).")
        return ""

    def boas_vindas(self, texto, midia):
        """Primeiro contato (ou o número passou a ser de outro funcionário): apresenta e já atende o pedido."""
        self.conversa.funcionario = self.funcionario
        self.mudar(Estado.ATENDIMENTO)
        registrar(LogAuditoria.Acao.WHATSAPP_PRIMEIRO_CONTATO, usuario=self.funcionario.usuario,
                  identificador=self.funcionario.matricula,
                  descricao=f"Número {self.conversa.numero[:4]}*****{self.conversa.numero[-4:]} "
                            f"({self.conversa_descricao})")
        boas_vindas = self.config.whatsapp_boas_vindas.replace("{nome}", self.funcionario.primeiro_nome)
        aviso = f"\n\n{self.config.whatsapp_aviso_privacidade}" if self.config.whatsapp_aviso_privacidade else ""
        self.responder(boas_vindas + aviso)
        pedido = interpretar(texto) if texto.strip() else None
        if midia or (pedido and pedido.acao not in (Acao.SAUDACAO, Acao.AJUDA, Acao.CONFIRMACAO)):
            return self.atender(texto, midia)
        self.mostrar_menu()

    # -----------------------------------------------------------------
    # Atendimento
    # -----------------------------------------------------------------
    def opcoes(self, nome="principal"):
        """As opções do menu. "Tirar uma dúvida" só aparece se o RH ligou na Configuração do portal."""
        if self.config.whatsapp_opcao_duvidas:
            return MENUS[nome]
        return [opcao for opcao in MENUS[nome] if opcao[1] != DUVIDA_ABERTA]

    def numero_da_opcao(self, acao):
        """O número que a opção tem no menu principal agora (ex.: falar com o RH = 5 ou 6)."""
        return next(n for n, (_, pedido) in enumerate(self.opcoes(), 1)
                    if isinstance(pedido, Pedido) and pedido.acao == acao)

    def menu(self, nome="principal"):
        opcoes = "\n".join(f"{n} - {rotulo}" for n, (rotulo, _) in enumerate(self.opcoes(nome), 1))
        titulo = "Sobre o vale-transporte, o que você precisa?" if nome == "vt" else "Posso te ajudar com:"
        return f"{titulo}\n{opcoes}\n\nResponda com o número ou escreva do seu jeito."

    def mostrar_menu(self, nome="principal", antes=""):
        """Mostra o menu e lembra qual foi, para entender a resposta só com o número."""
        self.mudar(Estado.ATENDIMENTO, menu=nome)
        self.responder(f"{antes}\n\n{self.menu(nome)}" if antes else self.menu(nome))

    def opcao_escolhida(self, texto):
        """O pedido da opção do último menu mostrado, se a pessoa respondeu só com o número."""
        achou = OPCAO_DE_MENU.fullmatch(texto)
        nome = self.conversa.contexto.get("menu")
        if not achou or not nome:
            return None
        numero = int(achou.group(1))
        if not 1 <= numero <= len(self.opcoes(nome)):
            return False
        return self.opcoes(nome)[numero - 1][1]

    def atender(self, texto, midia):
        conversa = self.conversa
        if midia:
            return self.receber_termo(midia)
        if not texto.strip():
            return self.responder("Por enquanto eu entendo mensagens escritas, fotos e PDFs. Me escreva o que "
                                  "você precisa, por favor.")
        if conversa.estado == Estado.FARDAMENTO:
            return self.continuar_fardamento(texto)
        if conversa.estado == Estado.AGUARDANDO_OPCAO_VT:
            escolha = opcao_vt(texto)
            if escolha:
                return self.criar_pedido_vt(escolha)
            if interpretar(texto).acao in (Acao.DUVIDA, Acao.SAUDACAO, Acao.AGRADECIMENTO, Acao.CONFIRMACAO):
                return self.responder("Para registrar o seu termo, responda 1 para UTILIZAR ou 2 para CANCELAR "
                                      "o vale-transporte.")
            self._descartar_midia()  # mudou de assunto: o termo guardado é descartado
        if conversa.estado == Estado.AGUARDANDO_MES:
            if conversa.contexto.get("opcoes") and ESCOLHA_NUMERADA.fullmatch(texto):
                return self.escolher_contracheque(texto)
            pedido = interpretar(texto)
            if pedido.mes or pedido.ultimo or pedido.acao == Acao.CONTRACHEQUE:
                pedido.acao = Acao.CONTRACHEQUE
                pedido.tipo_folha = pedido.tipo_folha or conversa.contexto.get("tipo_folha")
                return self.contracheque(pedido)

        escolhida = self.opcao_escolhida(texto) if conversa.estado == Estado.ATENDIMENTO else None
        if escolhida is False:
            nome = conversa.contexto["menu"]
            return self.responder(f"Escolha um número de 1 a {len(self.opcoes(nome))}, por favor.\n\n{self.menu(nome)}")
        if escolhida == DUVIDA_ABERTA:
            self.mudar(Estado.ATENDIMENTO)
            return self.responder("Pode escrever a sua dúvida que eu procuro a resposta para você.")
        pedido = escolhida or interpretar(texto)
        if pedido.acao == Acao.DUVIDA and not self._sensivel(texto):
            pedido = self._pedido_pela_ia(texto) or pedido
        acoes = {
            Acao.CONTRACHEQUE: self.contracheque, Acao.TERMO_VT: self.termo_vt, Acao.REGISTRO: self.registro,
            Acao.FARDAMENTO: lambda p: self.fardamento(texto),
            Acao.STATUS: self.status, Acao.ATENDENTE: lambda p: self.transferir("Pediu para falar com uma pessoa"),
            Acao.SAUDACAO: lambda p: self.mostrar_menu(antes=f"Olá, {self.funcionario.primeiro_nome}!"),
            Acao.AJUDA: lambda p: self.mostrar_menu(),
            Acao.VT: lambda p: self.mostrar_menu("vt"),
            Acao.AGRADECIMENTO: lambda p: self.encerrar("Por nada! Se precisar, é só chamar. 😊"),
            Acao.CONFIRMACAO: lambda p: self.encerrar("👍 Combinado! Quando precisar, é só me chamar."),
            Acao.DUVIDA: lambda p: self.duvida(texto),
        }
        if conversa.estado not in (Estado.ATENDIMENTO, Estado.AGUARDANDO_TERMO):
            self.mudar(Estado.ATENDIMENTO, **{k: v for k, v in conversa.contexto.items() if k == "opcao_vt"})
        acoes[pedido.acao](pedido)

    def rh_ja_encerrou(self):
        """
        O RH já respondeu e a conversa ficou parada por HORAS_PARA_VOLTAR_AO_ASSISTENTE (esqueceram de clicar em
        "Devolver ao assistente"). Quem ainda espera a primeira resposta do RH continua na fila.
        """
        ultima_do_rh = (self.conversa.mensagens.filter(direcao=Mensagem.Direcao.SAIDA, autor=Mensagem.Autor.RH)
                        .order_by("-criada_em").first())
        if ultima_do_rh is None:
            return False
        if self.conversa.aguardando_humano_desde and self.conversa.aguardando_humano_desde > ultima_do_rh.criada_em:
            return False  # pediu o RH de novo depois da última resposta: está esperando
        limite = timezone.now() - timedelta(hours=HORAS_PARA_VOLTAR_AO_ASSISTENTE)
        anteriores = self.conversa.mensagens.exclude(pk=self.conversa.mensagens.order_by("-criada_em", "-id").first().pk)
        ultima_atividade = anteriores.order_by("-criada_em").values_list("criada_em", flat=True).first()
        return ultima_do_rh.criada_em < limite and (ultima_atividade is None or ultima_atividade < limite)

    def ultima_resposta(self):
        return self.conversa.mensagens.filter(direcao=Mensagem.Direcao.SAIDA).order_by("-criada_em", "-id").first()

    def encerrar(self, texto):
        """Resposta curta de despedida; se a última já foi uma despedida ("ok" -> 👍 -> "ok"), fica quieto."""
        ultima = self.ultima_resposta()
        if ultima and ultima.texto in ENCERRAMENTOS:
            return
        self.responder(texto)

    def responder_uma_vez(self, texto):
        """Não repete o mesmo aviso em seguida (ex.: três áudios seguidos = um aviso só)."""
        ultima = self.ultima_resposta()
        if ultima and ultima.texto == texto and ultima.criada_em > timezone.now() - timedelta(minutes=10):
            return
        self.responder(texto)

    def _pedido_pela_ia(self, texto):
        dados = ia.classificar(texto, timezone.localdate())
        if not dados or dados.get("acao") not in ia.ACOES_IA:
            return None
        from .interpretacao import Pedido
        return Pedido(dados["acao"], mes=dados.get("mes") or None, ano=dados.get("ano") or None,
                      quantidade=max(1, min(dados.get("quantidade") or 1, 6)), ultimo=bool(dados.get("ultimo")),
                      tipo_folha=None if dados.get("tipo_folha") in (None, "mensal") else dados["tipo_folha"],
                      opcao_vt=None if dados.get("opcao_vt") in (None, "nenhuma") else dados["opcao_vt"])

    # --- Contracheque -------------------------------------------------
    def contracheque(self, pedido):
        disponiveis = self.funcionario.contracheques.order_by("-ano", "-mes", "tipo")
        if pedido.tipo_folha:
            disponiveis = disponiveis.filter(tipo=pedido.tipo_folha)
        nome_tipo = f" ({TipoFolha(pedido.tipo_folha).label})" if pedido.tipo_folha else ""
        if not disponiveis.exists():
            return self.responder(f"Ainda não há contracheque{nome_tipo} disponível para você. Assim que o RH "
                                  "publicar, ele fica disponível aqui.")
        if pedido.ultimo or pedido.quantidade > 1:
            mensais = disponiveis.filter(tipo=pedido.tipo_folha or TipoFolha.MENSAL)
            return self.enviar_contracheques(list(mensais[:pedido.quantidade]) or list(disponiveis[:pedido.quantidade]))
        if pedido.mes:
            ano = pedido.ano or date.today().year
            escolhidos = list(disponiveis.filter(mes=pedido.mes, ano=ano))
            if escolhidos:
                return self.enviar_contracheques(escolhidos)
            ano_listado = disponiveis.first().ano
            return self.listar_contracheques(
                disponiveis, tipo_folha=pedido.tipo_folha,
                aviso=f"Não encontrei o contracheque{nome_tipo} de {dict(MESES)[pedido.mes].lower()}/{ano}."
                      + ("" if ano == ano_listado else " Estes são os que tenho:"))
        if disponiveis.count() == 1:
            return self.enviar_contracheques(list(disponiveis))
        return self.listar_contracheques(disponiveis, tipo_folha=pedido.tipo_folha)

    def listar_contracheques(self, disponiveis, tipo_folha=None, aviso=""):
        """
        Os do ano mais recente, do mês mais antigo ao mais novo, e o número é o próprio mês (9 = setembro,
        13 = 13º salário). Outros anos: a pessoa escreve o mês e o ano. Férias/adiantamento/complementar
        aparecem embaixo, pedidos pelo nome (ex.: "férias julho").
        """
        ano = disponiveis.first().ano
        do_ano = sorted(disponiveis.filter(ano=ano), key=lambda c: c.mes)
        opcoes, linhas, outros = {}, [], []
        for c in do_ano:
            if tipo_folha or c.tipo == TipoFolha.MENSAL:
                numero, rotulo = c.mes, dict(MESES)[c.mes]
            elif c.tipo == TipoFolha.DECIMO_TERCEIRO:
                numero, rotulo = 13, "13º salário"
            else:
                outros.append(f"• {c.get_tipo_display()} de {dict(MESES)[c.mes].lower()}: escreva "
                              f"*{c.get_tipo_display().split()[-1].lower()} {dict(MESES)[c.mes].lower()}*")
                continue
            if str(numero) not in opcoes:
                opcoes[str(numero)] = c.pk
                linhas.append(f"{numero:02d} - {rotulo}")
        nome_tipo = f" ({TipoFolha(tipo_folha).label})" if tipo_folha else ""
        exemplo = list(opcoes)[-1] if opcoes else ""
        partes = [aviso] if aviso else []
        partes.append(f"📄 *Seus contracheques de {ano}{nome_tipo}*\n\n" + "\n".join(linhas))
        if outros:
            partes.append("Também disponível:\n" + "\n".join(outros))
        if opcoes:
            ex = f"*{exemplo}* para {dict(MESES).get(int(exemplo), '13º salário').lower()}"
            partes.append(f"Responda com o número do mês (ex.: {ex}"
                          + (f", ou *{list(opcoes)[-2]} e {exemplo}*)." if len(opcoes) > 1 else ")."))
        if disponiveis.exclude(ano=ano).exists():
            partes.append(f"Para outro ano, escreva o mês e o ano (ex.: março de {ano - 1}).")
        self.mudar(Estado.AGUARDANDO_MES, tipo_folha=tipo_folha, opcoes=opcoes)
        self.responder("\n\n".join(partes))

    def escolher_contracheque(self, texto):
        opcoes = self.conversa.contexto["opcoes"]
        if isinstance(opcoes, list):  # lista mostrada antes da mudança para "número = mês"
            opcoes = {str(n): pk for n, pk in enumerate(opcoes, 1)}
        numeros = [str(int(n)) for n in re.findall(r"\d{1,2}", texto)]
        if not numeros or any(n not in opcoes for n in numeros):
            validos = ", ".join(f"{int(n):02d}" for n in opcoes)
            return self.responder(f"Esse número não está na lista. Escolha um destes, por favor: {validos}.")
        ids = [opcoes[n] for n in dict.fromkeys(numeros)]
        por_id = self.funcionario.contracheques.in_bulk(ids)  # só os do próprio funcionário
        self.enviar_contracheques([por_id[i] for i in ids if i in por_id])

    def enviar_contracheques(self, contracheques):
        for c in contracheques:
            nome = f"contracheque_{c.ano}-{c.mes:02d}{'' if c.tipo == TipoFolha.MENSAL else '_' + c.tipo}.pdf"
            self.enviar_arquivo(c.arquivo, nome, descrever_contracheque(c, legenda=True), c)
        self.mudar(Estado.ATENDIMENTO)
        self.responder("Posso ajudar em mais alguma coisa?")

    # --- Registro de empregado ----------------------------------------
    def registro(self, pedido):
        documento = (self.funcionario.documentos.filter(tipo=TipoDocumento.REGISTRO_EMPREGADO)
                     .order_by("-enviado_em").first())
        if documento is None:
            return self.responder("Seu registro de empregado ainda não foi disponibilizado pelo RH.")
        self.enviar_arquivo(documento.arquivo, "registro_de_empregado.pdf", "Seu registro de empregado", documento)

    # --- Termo de VT --------------------------------------------------
    def termo_vt(self, pedido):
        em_andamento = vt_em_andamento(self.funcionario)
        if em_andamento:
            return self.responder(f"Você já tem um pedido de vale-transporte em andamento: protocolo "
                                  f"{em_andamento.protocolo} ({em_andamento.get_status_display().lower()}). Assim que "
                                  "o RH responder, eu te aviso aqui.")
        if not tipos_vt_disponiveis(self.funcionario):
            return self.responder("O pedido de vale-transporte não está disponível para o seu contrato. Procure o RH.")
        termo = termo_vt_do_funcionario(self.funcionario)  # individual, já preenchido (envio em lote)
        if termo is not None:
            arquivo, objeto, preencher = termo.arquivo, termo, ""
        elif self.config.termo_vt_padrao:  # formulário em branco, o mesmo para todos
            arquivo, objeto = self.config.termo_vt_padrao, self.config
            preencher = "Preencha os seus dados (nome, documentos e endereço), "
        else:
            return self.responder("Seu termo de vale-transporte ainda não foi disponibilizado pelo RH.")
        escolha = pedido.opcao_vt
        acao = {"vt_utilizar": "UTILIZAÇÃO", "vt_cancelar": "NÃO UTILIZAÇÃO (cancelamento)"}.get(escolha)
        self.enviar_arquivo(arquivo, "termo_vale_transporte.pdf", "Seu termo de vale-transporte", objeto)
        marque = (f"marque a opção de {acao} e assine." if acao else
                  "marque se quer UTILIZAR ou NÃO UTILIZAR (cancelar) o vale-transporte e assine.")
        instrucao = (preencher + marque) if preencher else marque[0].upper() + marque[1:]
        self.mudar(Estado.AGUARDANDO_TERMO, **({"opcao_vt": escolha} if escolha else {}))
        self.responder(f"{instrucao} Depois é só me mandar aqui uma foto nítida ou o PDF do termo assinado.")

    def receber_termo(self, midia):
        conteudo = midia.get("conteudo") or b""
        extensao = extensao_pelo_conteudo(conteudo[:8])
        if extensao is None or len(conteudo) > TAMANHO_MAXIMO_ANEXO_MB * 1024 * 1024:
            return self.responder(f"Não consegui usar esse arquivo. Mande uma foto (JPG/PNG) ou um PDF de até "
                                  f"{TAMANHO_MAXIMO_ANEXO_MB} MB, por favor.")
        em_andamento = vt_em_andamento(self.funcionario)
        if em_andamento:
            return self.responder(f"Você já tem um pedido de vale-transporte em andamento (protocolo "
                                  f"{em_andamento.protocolo}). Aguarde a resposta do RH antes de mandar outro termo.")
        escolha = self.conversa.contexto.get("opcao_vt")
        self._descartar_midia()  # mandou outra foto antes de escolher: a anterior não fica esquecida na pasta
        self.conversa.contexto = {"midia_pendente": True}
        self._guardar_midia(conteudo, extensao)
        if not escolha:
            self.mudar(Estado.AGUARDANDO_OPCAO_VT, **self.conversa.contexto)
            return self.responder("Recebi o termo! Você quer UTILIZAR ou CANCELAR o vale-transporte? "
                                  "Responda 1 para utilizar ou 2 para cancelar.")
        self.criar_pedido_vt(escolha)

    def _guardar_midia(self, conteudo, extensao):
        """O termo fica na pasta privada até o funcionário dizer se é para utilizar ou cancelar."""
        caminho = default_storage.save(f"whatsapp/recebidos/termo_{self.conversa.pk}{extensao}", ContentFile(conteudo))
        self.conversa.contexto.update({"midia_caminho": caminho, "midia_extensao": extensao})

    def _descartar_midia(self):
        caminho = self.conversa.contexto.get("midia_caminho")
        if caminho and default_storage.exists(caminho):
            default_storage.delete(caminho)

    def criar_pedido_vt(self, escolha):
        caminho = self.conversa.contexto.get("midia_caminho")
        if not caminho or not default_storage.exists(caminho):
            self.mudar(Estado.AGUARDANDO_TERMO, opcao_vt=escolha)
            return self.responder("Certo! Agora me mande a foto nítida ou o PDF do termo assinado.")
        tipos = tipos_vt_disponiveis(self.funcionario)
        if escolha not in tipos:
            self._descartar_midia()
            self.mudar(Estado.ATENDIMENTO)
            return self.responder("Esse pedido não está disponível para o seu contrato. Procure o RH.")
        with default_storage.open(caminho, "rb") as arquivo:
            solicitacao = criar_solicitacao_vt(self.funcionario, tipos[escolha], ContentFile(arquivo.read()),
                                               self.conversa.contexto.get("midia_extensao", ".jpg"),
                                               observacao="Enviado pelo WhatsApp.",
                                               origem=Solicitacao.Origem.WHATSAPP)
        default_storage.delete(caminho)
        registrar(LogAuditoria.Acao.WHATSAPP_TERMO_RECEBIDO, usuario=self.funcionario.usuario, objeto=solicitacao,
                  identificador=self.funcionario.matricula, descricao=f"{solicitacao.protocolo} ({self.conversa_descricao})")
        foto = (Mensagem.objects.filter(conversa=self.conversa, direcao=Mensagem.Direcao.ENTRADA, anexo_caminho="")
                .exclude(anexo_nome="").order_by("-criada_em").first())
        if foto:  # o RH abre o termo direto pelo histórico da conversa
            Mensagem.objects.filter(pk=foto.pk).update(anexo_caminho=solicitacao.termo_assinado.name)
        self.mudar(Estado.ATENDIMENTO)
        self.responder(f"Recebi seu termo! Seu pedido foi registrado com o protocolo {solicitacao.protocolo}. "
                       "Quando o RH responder, eu te aviso por aqui.")

    # --- Fardamento ---------------------------------------------------
    def fardamento(self, texto=""):
        """Pergunta as peças (pode ser mais de uma), o tamanho de cada uma e o motivo; vira um pedido com protocolo."""
        em_andamento = fardamento_em_andamento(self.funcionario)
        if em_andamento:
            return self.responder(f"Você já tem um pedido de fardamento em andamento: protocolo "
                                  f"{em_andamento.protocolo} ({em_andamento.get_status_display().lower()}). Assim "
                                  "que o RH responder, eu te aviso aqui.")
        if tipo_fardamento_disponivel(self.funcionario) is None:
            return self.responder("O pedido de fardamento não está disponível para o seu contrato. Procure o seu "
                                  "supervisor ou o RH.")
        pecas = pecas_citadas(texto, so_por_nome=True)  # "preciso de uma bota": já sabe a peça
        if pecas and "outra" not in pecas:
            return self._perguntar_tamanho({"etapa": "tamanho", "pecas": pecas, "tamanhos": []})
        self.mudar(Estado.FARDAMENTO, etapa="pecas")
        self.responder("Qual peça do fardamento você precisa?\n1 - Camisa\n2 - Calça\n3 - Bota / sapato\n"
                       "4 - Outra peça\n\nPode escolher mais de uma (ex.: *1 e 3*). Para desistir, escreva *cancelar*.")

    def continuar_fardamento(self, texto):
        contexto = dict(self.conversa.contexto)
        etapa = contexto.get("etapa")
        if normalizar(texto).strip() in ("cancelar", "cancela", "sair", "menu", "voltar", "desistir", "nao quero"):
            return self.mostrar_menu(antes="Tudo bem, o pedido de fardamento foi cancelado.")
        if etapa == "pecas":
            pecas = pecas_citadas(texto)
            if not pecas:
                return self.responder("Responda com o número das peças, por favor (ex.: *1* para camisa ou *1 e 3*).")
            contexto.update(pecas=pecas, tamanhos=[])
            if "outra" in pecas:
                self.mudar(Estado.FARDAMENTO, **{**contexto, "etapa": "outra"})
                return self.responder("Qual é a outra peça? (ex.: jaqueta, colete, boné)")
            return self._perguntar_tamanho({**contexto, "etapa": "tamanho"})
        if etapa == "outra":
            nome = " ".join(texto.split())[:60]
            if len(nome) < 3:
                return self.responder("Escreva o nome da peça, por favor (ex.: jaqueta).")
            return self._perguntar_tamanho({**contexto, "etapa": "tamanho", "outra": nome})
        if etapa == "tamanho":
            peca = contexto["pecas"][len(contexto["tamanhos"])]
            tamanho = tamanho_valido(peca, texto)
            if not tamanho:
                return self.responder(f"Não entendi o tamanho. {self._pergunta_tamanho(peca, contexto)}")
            contexto["tamanhos"].append(tamanho)
            if len(contexto["tamanhos"]) < len(contexto["pecas"]):
                return self._perguntar_tamanho(contexto)
            self.mudar(Estado.FARDAMENTO, **{**contexto, "etapa": "motivo"})
            return self.responder("Por que você precisa? (ex.: rasgou, desgastou, tamanho errado, posto novo)")
        if etapa == "motivo":
            return self._registrar_fardamento(contexto, " ".join(texto.split())[:300])
        return self.fardamento()  # contexto perdido: começa de novo

    def _pergunta_tamanho(self, peca, contexto):
        perguntas = {
            "camisa": "Qual o tamanho da *camisa*? (P, M, G, GG ou XG)",
            "calca": "Qual o tamanho da *calça*? (o número, ex.: 38, 40, 42)",
            "bota": "Qual o número da *bota / sapato*? (ex.: 38, 39, 40)",
        }
        return perguntas.get(peca) or f"Qual o tamanho de *{contexto.get('outra', 'outra peça')}*? (ex.: M, 40 ou ÚNICO)"

    def _perguntar_tamanho(self, contexto):
        self.mudar(Estado.FARDAMENTO, **contexto)
        self.responder(self._pergunta_tamanho(contexto["pecas"][len(contexto["tamanhos"])], contexto))

    def _registrar_fardamento(self, contexto, motivo):
        tipo = tipo_fardamento_disponivel(self.funcionario)
        if tipo is None or fardamento_em_andamento(self.funcionario):
            return self.fardamento()
        itens = [(peca, contexto.get("outra", "") if peca == "outra" else "", tamanho)
                 for peca, tamanho in zip(contexto["pecas"], contexto["tamanhos"])]
        solicitacao = criar_solicitacao_fardamento(self.funcionario, tipo, itens, motivo or "não informado")
        pecas = "\n".join(f"• {descricao or ItemFardamento.Peca(peca).label}: {tamanho}" for peca, descricao, tamanho in itens)
        self.mudar(Estado.ATENDIMENTO)
        self.responder(f"Pronto! Seu pedido de fardamento foi registrado com o protocolo {solicitacao.protocolo}:\n"
                       f"{pecas}\n\nQuando o RH responder, eu te aviso por aqui.")

    # --- Andamento dos pedidos ----------------------------------------
    def status(self, pedido):
        pedidos = list(self.funcionario.solicitacoes.select_related("tipo").order_by("-criado_em")[:3])
        if not pedidos:
            return self.responder("Você ainda não tem nenhum pedido registrado.")
        linhas = []
        for s in pedidos:
            linha = f"• {s.protocolo} — {s.tipo}: {s.get_status_display()}"
            if s.status == Solicitacao.Status.RECUSADA and s.motivo_recusa:
                linha += f". Motivo: {s.motivo_recusa}"
            elif s.resposta_rh:
                linha += f". RH: {s.resposta_rh}"
            if s.data_prevista_vigencia:
                linha += f" (vale a partir de {s.data_prevista_vigencia:%d/%m/%Y})"
            linhas.append(linha)
        self.responder("Seus pedidos mais recentes:\n" + "\n".join(linhas))

    # --- Dúvidas e transferência -------------------------------------
    def _sensivel(self, texto):
        t = normalizar(texto)
        return any(f" {palavra}" in t for palavra in ASSUNTOS_SENSIVEIS)

    def duvida(self, texto):
        if self._sensivel(texto):
            return self.transferir("Assunto que precisa de uma pessoa do RH")
        orientacoes = orientacoes_relacionadas(texto, self.funcionario)
        resposta = ia.responder_duvida(texto, [(o.titulo, f"{o.resumo}\n{o.conteudo}") for o in orientacoes])
        if resposta is None and orientacoes and not ia.ia_disponivel():
            melhor = orientacoes[0]
            resposta = f"*{melhor.titulo}*\n{melhor.resumo or melhor.conteudo[:600]}"
        if resposta:
            return self.responder(f"{resposta}\n\nSe não resolveu, escreva RH para falar com uma pessoa.")
        self.nao_entendi(texto)

    def nao_entendi(self, texto):
        """Não achou resposta: conversa, mostra as opções e oferece o RH, mas só transfere se a pessoa pedir."""
        tentativas = self.conversa.contexto.get("nao_entendi", 0) + 1
        if len(normalizar(texto).split()) >= 3:  # parece uma pergunta de verdade: o RH vê e completa a Central
            PerguntaSemResposta.objects.create(conversa=self.conversa, funcionario=self.funcionario, texto=texto[:2000])
        conversa_ia = ia.conversar(texto, [rotulo for rotulo, _ in self.opcoes()])
        dica = next((frase for palavras, frase in DICAS if any(f" {p}" in normalizar(texto) for p in palavras)), "")
        rh = self.numero_da_opcao(Acao.ATENDENTE)
        dica = dica.format(rh=rh, duvida=f"responda *{rh - 1}* e escreva a sua dúvida, ou "
                           if self.config.whatsapp_opcao_duvidas else "")
        if conversa_ia:
            antes = conversa_ia
        elif dica:
            antes = dica
        elif tentativas == 1:
            antes = "Desculpe, não entendi bem. Pode escrever de outro jeito, ou escolher uma das opções."
        else:
            antes = ("Ainda não consegui entender. Se preferir, uma pessoa do RH te ajuda: é só responder RH. "
                     "Ou escolha uma das opções.")
        self.mudar(Estado.ATENDIMENTO, menu="principal", nao_entendi=tentativas)
        self.responder(f"{antes}\n\n{self.menu()}")

    def receber_audio(self, midia):
        """
        O assistente não ouve áudio: guarda o arquivo e passa a conversa para o RH ouvir no painel.
        Número fora do cadastro não é encaminhado (não dá para saber quem é): pede para escrever.
        """
        conteudo = (midia or {}).get("conteudo") or b""
        extensao = ".ogg" if conteudo[:4] == b"OggS" else ".m4a" if conteudo[4:8] == b"ftyp" else None
        self.funcionario = identificar(self.conversa.numero)
        if (self.funcionario is None or not self.funcionario.pode_acessar or extensao is None
                or len(conteudo) > TAMANHO_MAXIMO_ANEXO_MB * 1024 * 1024):
            return self.responder_uma_vez(AVISO_DO_TIPO["audio"])
        caminho = default_storage.save(f"{PASTA_AUDIOS}audio_{self.conversa.pk}{extensao}", ContentFile(conteudo))
        recebida = (self.conversa.mensagens.filter(direcao=Mensagem.Direcao.ENTRADA, anexo_caminho="")
                    .order_by("-criada_em", "-id").first())
        if recebida:
            Mensagem.objects.filter(pk=recebida.pk).update(anexo_caminho=caminho, anexo_nome="áudio")
        if self.conversa.estado == Estado.HUMANO:
            return None  # já está com o RH: só guarda o áudio para ouvirem, sem o robô falar no meio
        self.conversa.funcionario = self.funcionario
        self.conversa.aguardando_humano_desde = timezone.now()
        self.mudar(Estado.HUMANO)
        registrar(LogAuditoria.Acao.WHATSAPP_TRANSFERIDO, usuario=self.funcionario.usuario,
                  identificador=self.funcionario.matricula, descricao=f"Mandou um áudio ({self.conversa_descricao})")
        self.responder("🎤 Recebi o seu áudio e passei para uma pessoa do RH ouvir. O atendimento humano funciona "
                       f"{self.config.whatsapp_horario_atendimento}. A resposta chega por aqui mesmo.")

    def transferir(self, motivo):
        conversa = self.conversa
        conversa.aguardando_humano_desde = timezone.now()
        self.mudar(Estado.HUMANO)
        registrar(LogAuditoria.Acao.WHATSAPP_TRANSFERIDO, usuario=self.funcionario.usuario,
                  identificador=self.funcionario.matricula, descricao=f"{motivo} ({self.conversa_descricao})")
        self.responder("Vou passar a sua mensagem para uma pessoa do RH. O atendimento humano funciona "
                       f"{self.config.whatsapp_horario_atendimento}. A resposta chega por aqui mesmo.")


# ---------------------------------------------------------------------------
# Apoio
# ---------------------------------------------------------------------------
CPF_NO_TEXTO = re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")


def esconder_cpf(texto):
    """LGPD: CPF digitado na conversa fica guardado mascarado (529.***.***-25). Só troca o que é CPF válido."""
    return CPF_NO_TEXTO.sub(lambda achado: mascarar_cpf(somente_digitos(achado.group()))
                            if cpf_e_valido(achado.group()) else achado.group(), texto)


PECA_PELO_NUMERO = {"1": "camisa", "2": "calca", "3": "bota", "4": "outra"}
PECA_PELO_NOME = {"camisa": "camisa", "camisas": "camisa", "camiseta": "camisa", "calca": "calca", "calcas": "calca",
                  "bota": "bota", "botas": "bota", "botina": "bota", "botinas": "bota", "sapato": "bota",
                  "sapatos": "bota", "coturno": "bota", "outra": "outra", "outro": "outra"}
TAMANHOS_EM_LETRA = {"PP", "P", "M", "G", "GG", "XG", "XGG", "EG", "EGG", "G1", "G2", "G3", "G4", "UNICO"}
TAMANHOS_POR_EXTENSO = {"PEQUENO": "P", "PEQUENA": "P", "MEDIO": "M", "MEDIA": "M", "GRANDE": "G"}


def pecas_citadas(texto, so_por_nome=False):
    """As peças pedidas, na ordem e sem repetir: pelo número da lista ("1 e 3") ou pelo nome ("camisa e bota")."""
    pecas = []
    for palavra in normalizar(texto).split():
        peca = PECA_PELO_NOME.get(palavra) or (None if so_por_nome else PECA_PELO_NUMERO.get(palavra))
        if peca and peca not in pecas:
            pecas.append(peca)
    return pecas


def tamanho_valido(peca, texto):
    """O tamanho já padronizado ("M", "42", "UNICO") ou '' se não fizer sentido para a peça."""
    t = normalizar(texto).upper()
    for palavra in ("TAMANHO", "NUMERO", "NO ", "N "):
        t = t.replace(palavra, " ")
    t = " ".join(t.split())
    numero = re.search(r"\b(\d{2})\b", t)
    if peca == "bota":
        return numero.group(1) if numero and 30 <= int(numero.group(1)) <= 50 else ""
    if peca == "calca" and numero:
        return numero.group(1) if 30 <= int(numero.group(1)) <= 70 else ""
    letra = TAMANHOS_POR_EXTENSO.get(t, t)
    if letra in TAMANHOS_EM_LETRA:
        return letra
    if peca == "outra" and 1 <= len(t) <= 10:
        return t
    return ""


def quer_vaga(texto):
    return _tem(normalizar(texto), PALAVRAS_DE_VAGA)


def identificar(numero):
    """O funcionário dono deste número no cadastro. Nunca pelo nome do perfil do WhatsApp."""
    candidatos = list(Funcionario.objects.filter(whatsapp__in=variantes(numero)).select_related("usuario")[:2])
    return candidatos[0] if len(candidatos) == 1 else None  # número repetido no cadastro: não entrega nada


def orientacoes_relacionadas(texto, funcionario, limite=5):
    """Orientações publicadas para o contrato do funcionário, das mais parecidas com a pergunta."""
    palavras = {p for p in normalizar(texto).split() if len(p) > 2 and p not in PALAVRAS_POUCO_UTEIS}
    if not palavras:
        return []
    pontuadas = []
    for orientacao in Orientacao.objects.publicadas_para(funcionario):
        conteudo = normalizar(f"{orientacao.titulo} {orientacao.titulo} {orientacao.resumo} {orientacao.conteudo}")
        pontos = sum(conteudo.count(f" {p}") for p in palavras)
        if pontos:
            pontuadas.append((pontos, orientacao))
    pontuadas.sort(key=lambda item: -item[0])
    return [o for _, o in pontuadas[:limite]]


def descrever_contracheque(contracheque, legenda=False):
    """'Agosto de 2026' (folha mensal) ou 'Dezembro de 2025 - 13º salário'."""
    texto = contracheque.competencia_por_extenso
    if contracheque.tipo != TipoFolha.MENSAL:
        texto += f" - {contracheque.get_tipo_display()}"
    return f"Contracheque de {texto.lower()}" if legenda else texto


def link_seguro(objeto, funcionario):
    """Link que só abre aquele documento, daquele funcionário, por 24 horas (e fica na auditoria)."""
    if isinstance(objeto, Contracheque):
        tipo = "contracheque"
    elif isinstance(objeto, ConfiguracaoPortal):
        tipo = "termo_padrao"  # o formulário em branco do termo de VT (o mesmo para todos)
    else:
        tipo = "documento"
    token = TimestampSigner(salt=SALT_LINK).sign_object({"t": tipo, "id": objeto.pk, "f": funcionario.pk})
    return settings.PORTAL_URL.rstrip("/") + reverse("whatsapp:documento", args=[token])
