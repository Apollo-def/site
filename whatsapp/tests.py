import base64
import hashlib
import hmac
import io
import json
import os
from datetime import date, timedelta
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook
from django.utils import timezone

from auditoria.models import LogAuditoria
from contas.models import ConfiguracaoPortal
from contracheques.models import Contracheque
from contracheques.tests import Base, gerar_pdf, pagina_contracheque
from documentos.models import DocumentoFuncionario
from orientacoes.models import Categoria, Orientacao
from solicitacoes.models import ItemFardamento, Solicitacao, TipoSolicitacao
from solicitacoes.servicos import criar_solicitacao_fardamento, tipo_fardamento_disponivel, tipos_vt_disponiveis

from . import atendimento, ia
from .atendimento import receber
from .interpretacao import Acao, interpretar
from .models import Conversa, Mensagem, PedidoCadastro, PerguntaSemResposta
from .notificacoes import conversa_do_funcionario
from .numeros import normalizar_celular, variantes

CEL_CLARA = "5598987654321"
PDF = b"%PDF-1.4 termo assinado"


class NumerosTestes(SimpleTestCase):
    def test_normaliza_celular_e_aceita_sem_o_nove(self):
        self.assertEqual(normalizar_celular("(98) 98765-4321"), CEL_CLARA)
        self.assertEqual(normalizar_celular("98 8765-4321"), CEL_CLARA)       # celular antigo, sem o 9
        self.assertEqual(normalizar_celular("+55 98 98765-4321"), CEL_CLARA)
        self.assertEqual(normalizar_celular("(98) 3232-1010"), "")            # telefone fixo
        self.assertEqual(variantes("559887654321"), {CEL_CLARA, "559887654321"})

    def test_interpretacao(self):
        hoje = date(2026, 10, 1)
        p = interpretar("boa tarde, preciso do contracheque do mês passado", hoje)
        self.assertEqual((p.acao, p.mes, p.ano), (Acao.CONTRACHEQUE, 9, 2026))
        self.assertEqual(interpretar("quero cancelar o vale transporte", hoje).opcao_vt, "vt_cancelar")
        self.assertEqual(interpretar("quanto é descontado do vale transporte?", hoje).acao, Acao.DUVIDA)
        self.assertEqual(interpretar("os 3 últimos contracheques", hoje).quantidade, 3)

    def test_respostas_curtas_e_erros_de_digitacao(self):
        hoje = date(2026, 10, 1)
        for texto in ("Ok !", "ok", "ta", "ta bom", "só isso", "beleza, entendi", "👍", "🙏🙏", "não"):
            self.assertEqual(interpretar(texto, hoje).acao, Acao.CONFIRMACAO, texto)
        for texto in ("sim", "quero", "sim por favor"):
            self.assertEqual(interpretar(texto, hoje).acao, Acao.AJUDA, texto)
        self.assertNotEqual(interpretar("?", hoje).acao, Acao.CONFIRMACAO)
        self.assertNotEqual(interpretar("ok, mas e o meu vale?", hoje).acao, Acao.CONFIRMACAO)
        p = interpretar("contraxeque de agosot", hoje)
        self.assertEqual((p.acao, p.mes), (Acao.CONTRACHEQUE, 8))
        self.assertEqual(interpretar("holerit do mes passado", hoje).acao, Acao.CONTRACHEQUE)
        self.assertEqual(interpretar("quero cancelar o vale tranporte", hoje).opcao_vt, "vt_cancelar")
        self.assertEqual(interpretar("andamneto do meu pedido", hoje).acao, Acao.STATUS)

    def test_ia_nunca_recebe_cpf_data_ou_telefone(self):
        texto = ia.anonimizar("meu cpf 529.982.247-25, nasci 07/03/1990, cel (98) 98765-4321, matrícula 700013")
        for dado in ("529", "1990", "98765", "700013"):
            self.assertNotIn(dado, texto)


class AssistenteBase(Base):
    def setUp(self):
        super().setUp()
        self.clara.telefone = "(98) 98765-4321"
        self.clara.save()
        self.joao.telefone = "98 8123-4567"  # sem o 9: o WhatsApp manda 5598981234567
        self.joao.save()
        config = ConfiguracaoPortal.atual()
        config.whatsapp_ativo = True
        config.save()

    def falar(self, texto="", midia=None, numero=CEL_CLARA):
        return receber(numero, texto=texto, midia=midia, simulada=True)

    def respostas(self, numero=CEL_CLARA):
        return list(Mensagem.objects.filter(conversa__numero=numero, direcao="saida").values_list("texto", flat=True))

    def ultima(self, numero=CEL_CLARA):
        return self.respostas(numero)[-1]

    def comecar(self, primeira="oi", numero=CEL_CLARA):
        """Primeiro contato: o assistente reconhece pelo celular do cadastro, sem pedir CPF."""
        return self.falar(primeira, numero=numero)

    def publicar_contracheques(self, *meses):
        for mes in meses:
            self.enviar(gerar_pdf([pagina_contracheque("7013", "CLARA MARIA SILVA SANTOS", f"Mês {mes} de 2026")]),
                        mes=mes, ano=2026)

    def dar_termo_vt(self, funcionario=None):
        documento = DocumentoFuncionario(funcionario=funcionario or self.clara, tipo="termo_vt", titulo="Termo de VT")
        documento.arquivo.save("termo.pdf", ContentFile(gerar_pdf([["TERMO DE OPCAO - VALE TRANSPORTE"]])), save=False)
        documento.save()


class IdentificacaoTestes(AssistenteBase):
    def test_numero_desconhecido_nao_recebe_nada(self):
        self.falar("quero meu contracheque", numero="5598911112222")
        self.assertIn("não encontrei este número", self.ultima("5598911112222"))
        self.assertIn("nome completo", self.ultima("5598911112222"))
        self.assertFalse(Mensagem.objects.exclude(anexo_nome="").exists())

    def test_reconhece_pelo_celular_sem_pedir_cpf(self):
        self.comecar()
        boas_vindas, menu = self.respostas()
        self.assertIn("Clara", boas_vindas)
        self.assertIn("Posso te ajudar", menu)
        self.assertNotIn("CPF", boas_vindas + menu)
        self.assertEqual(Conversa.objects.get().funcionario, self.clara)
        self.assertTrue(LogAuditoria.objects.filter(acao="whatsapp_primeiro_contato").exists())
        self.falar("obrigada")
        self.assertEqual(len(self.respostas()), 3)  # não se apresenta de novo

    def test_celular_cadastrado_sem_o_nove(self):
        self.comecar(numero="559881234567")
        self.assertIn("Joao", self.respostas("559881234567")[0])

    def test_desligado_sem_permissao_nao_recebe_nada(self):
        self.clara.situacao, self.clara.inativo_pode_acessar = "inativo", False
        self.clara.save()
        self.comecar("contracheque de janeiro")
        self.assertIn("desativado", self.ultima())
        self.assertEqual(len(self.respostas()), 1)

    def test_numero_repassado_para_outro_funcionario(self):
        self.comecar()
        # O RH tira o número da Clara e põe no cadastro do João: a conversa passa a ser do João.
        self.clara.telefone = "(98) 99999-0000"
        self.clara.save()
        self.joao.telefone = "(98) 98765-4321"
        self.joao.save()
        self.falar("meu contracheque")
        self.assertEqual(Conversa.objects.get().funcionario, self.joao)
        self.assertTrue(any("Joao" in r for r in self.respostas()[2:]))

    def test_numero_repetido_no_cadastro_nao_recebe_nada(self):
        self.joao.telefone = "(98) 98765-4321"  # mesmo celular da Clara
        self.joao.save()
        self.comecar("contracheque de janeiro")
        self.assertIn("não encontrei este número", self.ultima())

    def test_aviso_do_rh_nao_vai_para_numero_antigo(self):
        self.comecar()
        self.assertIsNotNone(conversa_do_funcionario(self.clara))
        self.clara.telefone = "(98) 99999-0000"
        self.clara.save()
        self.assertIsNone(conversa_do_funcionario(self.clara))

    def test_atendimento_desligado(self):
        config = ConfiguracaoPortal.atual()
        config.whatsapp_ativo = False
        config.save()
        receber(CEL_CLARA, texto="oi")  # conversa real (não simulador)
        self.assertIn("desligado", Mensagem.objects.filter(direcao="saida").last().texto)


class DocumentosTestes(AssistenteBase):
    def test_pedido_ja_e_atendido_no_primeiro_contato(self):
        self.publicar_contracheques(1)
        self.comecar("me manda o contracheque de janeiro de 2026")
        enviado = Mensagem.objects.filter(direcao="saida").exclude(anexo_nome="").get()
        self.assertEqual(enviado.anexo_nome, "contracheque_2026-01.pdf")
        cc = Contracheque.objects.get(funcionario=self.clara)
        self.assertTrue(LogAuditoria.objects.filter(acao="whatsapp_documento_enviado", objeto_id=str(cc.pk)).exists())

    def anexos(self):
        return list(Mensagem.objects.exclude(anexo_nome="").values_list("anexo_nome", flat=True))

    def test_mostra_os_contracheques_numerados_e_envia_o_escolhido(self):
        self.publicar_contracheques(1, 2, 3)
        self.comecar()
        self.falar("preciso do meu contracheque")
        lista = self.ultima()
        self.assertIn("*Seus contracheques de 2026*\n\n01 - Janeiro\n02 - Fevereiro\n03 - Março", lista)
        self.assertNotIn("outro ano", lista)
        self.falar("2")
        self.assertEqual(self.anexos(), ["contracheque_2026-02.pdf"])
        self.falar("contracheque")
        self.falar("03 e 1")
        self.assertEqual(self.anexos()[1:], ["contracheque_2026-03.pdf", "contracheque_2026-01.pdf"])

    def test_numero_fora_da_lista_e_mes_que_nao_existe(self):
        self.publicar_contracheques(1, 2)
        self.comecar()
        self.falar("contracheque")
        self.falar("7")
        self.assertIn("Escolha um destes, por favor: 01, 02", self.ultima())
        self.falar("contracheque de maio")
        self.assertIn("Não encontrei o contracheque de maio/2026", self.ultima())
        self.assertIn("02 - Fevereiro", self.ultima())
        self.falar("os 2 últimos")
        self.assertEqual(self.anexos(), ["contracheque_2026-02.pdf", "contracheque_2026-01.pdf"])

    def test_outro_ano_13o_e_ferias(self):
        def criar(mes, ano, tipo="mensal"):
            cc = Contracheque(funcionario=self.clara, mes=mes, ano=ano, tipo=tipo)
            cc.arquivo.save("c.pdf", ContentFile(gerar_pdf([["CONTRACHEQUE"]])), save=False)
            cc.save()

        criar(11, 2025)
        criar(12, 2025, "13")
        criar(1, 2026)
        criar(2, 2026)
        criar(1, 2026, "ferias")
        self.comecar()
        self.falar("contracheque")
        lista = self.ultima()
        self.assertIn("01 - Janeiro\n02 - Fevereiro", lista)
        self.assertNotIn("Novembro", lista)  # só o ano mais recente; os outros, pelo mês e ano
        self.assertIn("Férias de janeiro: escreva *férias janeiro*", lista)
        self.assertIn("março de 2025", lista)
        self.falar("férias janeiro")
        self.assertEqual(self.anexos(), ["contracheque_2026-01_ferias.pdf"])
        self.falar("contracheque de dezembro de 2025")
        self.assertEqual(self.anexos()[-1], "contracheque_2025-12_13.pdf")

    def test_com_um_so_contracheque_envia_direto(self):
        self.publicar_contracheques(1)
        self.comecar()
        self.falar("meu contracheque")
        self.assertEqual(self.anexos(), ["contracheque_2026-01.pdf"])

    @override_settings(PORTAL_URL="https://portal.teste")
    def test_envio_por_link_de_24_horas(self):
        self.publicar_contracheques(1)
        config = ConfiguracaoPortal.atual()
        config.whatsapp_forma_envio = "link"
        config.save()
        self.comecar("contracheque de janeiro de 2026")
        texto = Mensagem.objects.exclude(anexo_nome="").get().texto
        link = texto.split("https://portal.teste")[1].split()[0]
        self.client.logout()
        resposta = self.client.get(link)
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(b"".join(resposta.streaming_content).startswith(b"%PDF"))
        self.assertEqual(self.client.get(link[:-3] + "xyz/").status_code, 404)  # link adulterado
        with mock.patch("django.core.signing.time.time", return_value=timezone.now().timestamp() + 25 * 3600):
            self.assertEqual(self.client.get(link).status_code, 404)  # vencido


class TermoVTPadraoTestes(AssistenteBase):
    """O formulário em branco (Configuração) vale para quem não tem termo individual."""

    def setUp(self):
        super().setUp()
        config = ConfiguracaoPortal.atual()
        config.termo_vt_padrao.save("termo.pdf", ContentFile(gerar_pdf([["DECLARACAO DE OPCAO VT"]])), save=True)
        self.comecar()

    def test_envia_o_termo_padrao_e_recebe_de_volta(self):
        self.falar("quero o termo do vale transporte")
        enviado = Mensagem.objects.exclude(anexo_nome="").get()
        self.assertEqual(enviado.anexo_nome, "termo_vale_transporte.pdf")
        self.assertIn("Preencha os seus dados", self.ultima())
        self.falar(midia={"conteudo": PDF, "nome": "termo.pdf"})
        self.falar("2")
        self.assertEqual(Solicitacao.objects.get().tipo.codigo, "vt_cancelar")

    def test_termo_individual_tem_preferencia(self):
        self.dar_termo_vt()
        self.falar("quero o termo do vale transporte")
        self.assertNotIn("Preencha os seus dados", self.ultima())
        self.assertTrue(self.ultima().startswith("Marque"))

    @override_settings(PORTAL_URL="https://portal.teste")
    def test_link_de_24_horas_do_termo_padrao(self):
        config = ConfiguracaoPortal.atual()
        config.whatsapp_forma_envio = "link"
        config.save()
        self.falar("termo do vale transporte")
        texto = Mensagem.objects.exclude(anexo_nome="").get().texto
        link = texto.split("https://portal.teste")[1].split()[0]
        resposta = self.client.get(link)
        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(b"".join(resposta.streaming_content).startswith(b"%PDF"))


class TermoVTTestes(AssistenteBase):
    def setUp(self):
        super().setUp()
        self.dar_termo_vt()
        self.comecar()

    def test_cancelar_vt_ponta_a_ponta(self):
        self.falar("quero cancelar o vale transporte")
        self.assertIn("NÃO UTILIZAÇÃO", self.ultima())
        self.falar(midia={"conteudo": PDF, "nome": "termo.pdf"})
        pedido = Solicitacao.objects.get()
        self.assertEqual((pedido.tipo.codigo, pedido.origem, pedido.status), ("vt_cancelar", "whatsapp", "pendente"))
        self.assertIn(pedido.protocolo, self.ultima())
        self.falar(midia={"conteudo": PDF, "nome": "termo.pdf"})
        self.assertIn("já tem um pedido", self.ultima())
        self.assertEqual(Solicitacao.objects.count(), 1)

    def test_foto_primeiro_e_depois_a_opcao(self):
        self.falar(midia={"conteudo": b"\xff\xd8\xff\xe0fotojpg", "nome": "foto.jpg"})
        self.assertIn("1 para utilizar", self.ultima())
        self.falar("1")
        pedido = Solicitacao.objects.get()
        self.assertEqual(pedido.tipo.codigo, "vt_utilizar")
        self.assertTrue(pedido.termo_assinado.name.endswith(".jpg"))

    def test_segunda_foto_antes_da_opcao_nao_deixa_a_primeira_esquecida(self):
        self.falar(midia={"conteudo": b"\xff\xd8\xff\xe0primeira", "nome": "foto.jpg"})
        self.falar(midia={"conteudo": b"\xff\xd8\xff\xe0segunda", "nome": "foto.jpg"})
        self.assertEqual(len(default_storage.listdir("whatsapp/recebidos")[1]), 1)
        self.falar("2")
        self.assertEqual(Solicitacao.objects.get().termo_assinado.read(), b"\xff\xd8\xff\xe0segunda")
        self.assertEqual(default_storage.listdir("whatsapp/recebidos")[1], [])

    def test_opcao_indisponivel_descarta_o_termo_recebido(self):
        tipos_vt_disponiveis(self.clara)  # cria os tipos de VT
        TipoSolicitacao.objects.filter(codigo="vt_utilizar").update(ativo=False)
        self.falar(midia={"conteudo": b"\xff\xd8\xff\xe0foto", "nome": "foto.jpg"})
        caminho = Conversa.objects.get().contexto["midia_caminho"]
        self.falar("1")
        self.assertIn("não está disponível", self.ultima())
        self.assertFalse(default_storage.exists(caminho))
        self.assertEqual(Conversa.objects.get().estado, Conversa.Estado.ATENDIMENTO)

    def test_limpeza_apaga_termo_abandonado(self):
        self.falar(midia={"conteudo": b"\xff\xd8\xff\xe0foto", "nome": "foto.jpg"})
        caminho = Conversa.objects.get().contexto["midia_caminho"]
        call_command("limpar_whatsapp", stdout=io.StringIO())
        self.assertTrue(default_storage.exists(caminho))  # recente: a pessoa ainda pode escolher
        antigo = (timezone.now() - timedelta(days=8)).timestamp()
        os.utime(default_storage.path(caminho), (antigo, antigo))
        call_command("limpar_whatsapp", stdout=io.StringIO())
        self.assertFalse(default_storage.exists(caminho))
        self.falar("1")
        self.assertIn("me mande a foto", self.ultima())  # voltou depois: o assistente pede de novo
        self.assertFalse(Solicitacao.objects.exists())

    def test_arquivo_invalido(self):
        self.falar(midia={"conteudo": b"MZ nao sou foto", "nome": "x.exe"})
        self.assertIn("Não consegui usar esse arquivo", self.ultima())
        self.assertFalse(Solicitacao.objects.exists())

    def test_resposta_do_rh_chega_no_whatsapp(self):
        self.falar("quero utilizar o vale transporte")
        self.falar(midia={"conteudo": PDF, "nome": "termo.pdf"})
        pedido = Solicitacao.objects.get()
        self.client.force_login(self.rh)
        url = reverse("admin:solicitacoes_solicitacao_change", args=[pedido.pk])
        dados = {"funcionario": self.clara.pk, "tipo": pedido.tipo.pk, "status": "recusada",
                 "motivo_recusa": "Termo sem assinatura.", "historico-TOTAL_FORMS": 0, "historico-INITIAL_FORMS": 1}
        self.client.post(url, {**dados, "historico-INITIAL_FORMS": pedido.historico.count()})
        self.assertIn("Recusada", self.ultima())
        self.assertIn("Termo sem assinatura.", self.ultima())


class DuvidasETransferenciaTestes(AssistenteBase):
    def setUp(self):
        super().setUp()
        self.comecar()

    def test_responde_com_a_central_de_orientacoes(self):
        Orientacao.objects.create(titulo="Desconto do vale-transporte", categoria=Categoria.objects.create(nome="VT"),
                                  resumo="O desconto é de até 6% do salário base.", status="publicado")
        self.falar("quanto é descontado do vale transporte?")
        self.assertIn("até 6%", self.ultima())

    def test_sem_resposta_continua_conversando_e_so_vai_para_o_rh_se_pedir(self):
        self.falar("posso trocar meu horário de almoço?")
        self.assertTrue(PerguntaSemResposta.objects.filter(texto__contains="almoço").exists())
        self.assertEqual(Conversa.objects.get().estado, Conversa.Estado.ATENDIMENTO)  # não transferiu sozinho
        self.assertIn("não entendi", self.ultima())
        self.assertIn("1 - Contracheque", self.ultima())
        self.falar("e o horário de almoço como fica")
        self.assertIn("responder RH", self.ultima())
        self.falar("rh")
        self.assertEqual(Conversa.objects.get().estado, Conversa.Estado.HUMANO)
        self.falar("alô?")  # com o RH: o assistente não responde
        self.assertIn("pessoa do RH", self.ultima())

    def test_assunto_sensivel_vai_direto_para_o_rh(self):
        self.falar("sofri um acidente no trabalho, o que faço?")
        self.assertEqual(Conversa.objects.get().estado, Conversa.Estado.HUMANO)
        self.assertFalse(PerguntaSemResposta.objects.exists())

    def test_rh_responde_e_devolve_ao_assistente(self):
        self.falar("quero falar com atendente")
        conversa = Conversa.objects.get()
        self.client.force_login(self.rh)
        url = reverse("admin:whatsapp_atender", args=[conversa.pk])
        self.client.post(url, {"texto": "Olá, Clara! Já vou verificar."})
        self.assertEqual(Mensagem.objects.filter(autor="rh").get().texto, "Olá, Clara! Já vou verificar.")
        self.client.post(url, {"devolver": "1"})
        conversa.refresh_from_db()
        self.assertEqual(conversa.estado, Conversa.Estado.ATENDIMENTO)

    @override_settings(WHATSAPP_IA_LIGADA=True)
    def test_ia_responde_so_com_os_textos_e_sem_cpf(self):
        Orientacao.objects.create(titulo="Férias", categoria=Categoria.objects.create(nome="Férias"),
                                  conteudo="As férias são marcadas pelo supervisor com 30 dias de antecedência.",
                                  status="publicado")
        chamadas = []

        def falsa(sistema, conteudo, esquema):
            chamadas.append(conteudo)
            if "sabe" in esquema["properties"]:
                return {"sabe": True, "resposta": "Fale com seu supervisor com 30 dias de antecedência."}
            return {"acao": "duvida", "mes": 0, "ano": 0, "quantidade": 1, "ultimo": False, "tipo_folha": "mensal",
                    "opcao_vt": "nenhuma"}

        with mock.patch.object(ia, "_chamar", side_effect=falsa):
            self.falar("como marco minhas férias? meu cpf é 529.982.247-25")
        self.assertIn("30 dias", self.ultima())
        self.assertTrue(chamadas)
        self.assertFalse(any("529" in c for c in chamadas))


@override_settings(WHATSAPP_CANAL="meta", WHATSAPP_APP_SECRET="segredo", WHATSAPP_VERIFY_TOKEN="senha-webhook",
                   WHATSAPP_TOKEN="token", WHATSAPP_PHONE_NUMBER_ID="123")
class WebhookTestes(AssistenteBase):
    url = "/whatsapp/webhook/"

    def test_confirmacao_do_endereco(self):
        ok = self.client.get(self.url, {"hub.mode": "subscribe", "hub.verify_token": "senha-webhook",
                                        "hub.challenge": "42"})
        self.assertEqual(ok.content, b"42")
        errado = self.client.get(self.url, {"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "42"})
        self.assertEqual(errado.status_code, 403)

    def corpo(self, texto):
        return json.dumps({"entry": [{"changes": [{"value": {"messages": [
            {"from": CEL_CLARA, "id": "wamid.1", "type": "text", "text": {"body": texto}}]}}]}]}).encode()

    def test_sem_assinatura_da_meta_e_recusado(self):
        resposta = self.client.post(self.url, self.corpo("oi"), content_type="application/json")
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(Conversa.objects.exists())

    def test_mensagem_assinada_e_atendida_e_repetida_e_ignorada(self):
        corpo = self.corpo("oi")
        assinatura = "sha256=" + hmac.new(b"segredo", corpo, hashlib.sha256).hexdigest()
        enviado = []
        with mock.patch("whatsapp.canal.CanalMeta._pedido",
                        side_effect=lambda *a, **k: enviado.append(a) or b'{"messages": [{"id": "wamid.r"}]}'):
            for _ in range(2):  # a Meta pode reenviar a mesma mensagem
                resposta = self.client.post(self.url, corpo, content_type="application/json",
                                            headers={"X-Hub-Signature-256": assinatura})
                self.assertEqual(resposta.status_code, 200)
        conversa = Conversa.objects.get(simulada=False)
        self.assertEqual(conversa.mensagens.filter(direcao="entrada").count(), 1)
        self.assertEqual(len(enviado), 2)  # boas-vindas + menu
        self.assertIn("Clara", conversa.mensagens.filter(direcao="saida").first().texto)


@override_settings(WHATSAPP_CANAL="meta")
class PainelTestes(AssistenteBase):
    def test_simulador_e_coluna_de_funcionarios(self):
        self.client.force_login(self.rh)
        url = reverse("admin:whatsapp_simulador")
        self.client.post(url, {"numero": "(98) 98765-4321", "texto": "oi"})
        self.assertContains(self.client.get(url, {"numero": CEL_CLARA}), "Posso te ajudar")
        self.assertContains(self.client.get(reverse("admin:contas_funcionario_changelist")), "📱 cadastrado")
        self.assertEqual(self.client.get(reverse("admin:whatsapp_conexao")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:whatsapp_conversa_changelist")).status_code, 200)


TOKEN_GATEWAY = "senha-do-gateway-com-mais-de-20"
CEL_NOVO_GATEWAY = "5598933334444"


@override_settings(WHATSAPP_CANAL="gateway", WHATSAPP_GATEWAY_TOKEN=TOKEN_GATEWAY)
class GatewayTestes(AssistenteBase):
    url = "/whatsapp/gateway/"

    def postar(self, dados, token=TOKEN_GATEWAY):
        return self.client.post(self.url, json.dumps(dados), content_type="application/json",
                                headers={"X-Gateway-Token": token})

    def test_sem_a_senha_do_gateway_e_recusado(self):
        self.assertEqual(self.postar({"de": CEL_CLARA, "id": "1", "texto": "oi"}, token="errada").status_code, 403)
        with override_settings(WHATSAPP_CANAL="meta"):
            self.assertEqual(self.postar({"de": CEL_CLARA, "id": "1", "texto": "oi"}).status_code, 403)
        self.assertFalse(Conversa.objects.exists())

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r"^whatsapp/gateway/$"])
    def test_gateway_local_nao_e_mandado_para_https(self):
        self.assertEqual(self.postar({"de": CEL_CLARA, "id": "x"}, token="errada").status_code, 403)  # não 301
        self.assertEqual(self.client.get("/").status_code, 301)  # o resto continua indo para https

    def test_mensagem_vai_e_volta_pelo_gateway_sem_repetir(self):
        enviado = []
        with mock.patch("whatsapp.canal.CanalGateway._pedido",
                        side_effect=lambda caminho, dados=None, **k: enviado.append((caminho, dados)) or {"id": "r1"}):
            for _ in range(2):
                self.assertEqual(self.postar({"de": CEL_CLARA, "id": "msg1", "texto": "oi"}).status_code, 200)
        conversa = Conversa.objects.get(simulada=False)
        self.assertEqual(conversa.mensagens.filter(direcao="entrada").count(), 1)
        self.assertEqual(len(enviado), 2)  # boas-vindas + menu
        caminho, dados = enviado[0]
        self.assertEqual((caminho, dados["para"], dados["tipo"]), ("/enviar", CEL_CLARA, "texto"))
        self.assertIn("Clara", dados["texto"])

    def test_sinal_vazio_do_whatsapp_nao_gera_resposta_em_dobro(self):
        with mock.patch("whatsapp.canal.CanalGateway._pedido", return_value={"id": "r"}) as enviou:
            self.postar({"de": CEL_CLARA, "id": "m1", "texto": "oi"})
            respostas = enviou.call_count
            self.postar({"de": CEL_CLARA, "id": "m2", "texto": ""})
        self.assertEqual(enviou.call_count, respostas)
        self.assertEqual(Mensagem.objects.filter(direcao="entrada").count(), 1)

    def test_audio_e_ligacao_pedem_para_escrever(self):
        with mock.patch("whatsapp.canal.CanalGateway._pedido", return_value={"id": "r"}):
            self.postar({"de": CEL_CLARA, "id": "a1", "texto": "", "tipo": "audio"})
            self.assertIn("não consigo ouvir áudios", self.ultima())
            total = len(self.respostas())
            self.postar({"de": CEL_CLARA, "id": "a2", "texto": "", "tipo": "audio"})  # outro áudio: um aviso só
            self.assertEqual(len(self.respostas()), total)
            self.postar({"de": CEL_NOVO_GATEWAY, "id": "ligacao-1", "texto": "", "tipo": "ligacao"})  # fora do cadastro
            self.assertIn("Não consigo atender ligações", self.ultima(CEL_NOVO_GATEWAY))
            self.postar({"de": CEL_NOVO_GATEWAY, "id": "ligacao-2", "texto": "", "tipo": "ligacao"})
            self.assertEqual(len([t for t in self.respostas(CEL_NOVO_GATEWAY) if "ligações" in t]), 2)  # toda ligação
            self.postar({"de": CEL_CLARA, "id": "f1", "texto": "", "tipo": "figurinha"})
        self.assertIn("[áudio]", list(Mensagem.objects.filter(direcao="entrada").values_list("texto", flat=True)))

    def test_audio_do_funcionario_vai_para_o_rh_ouvir(self):
        audio = {"base64": base64.b64encode(b"OggS\x00\x02audio de teste").decode(), "nome": "audio.ogg"}
        with mock.patch("whatsapp.canal.CanalGateway._pedido", return_value={"id": "r"}):
            self.postar({"de": CEL_CLARA, "id": "oi", "texto": "oi"})
            self.postar({"de": CEL_CLARA, "id": "a1", "texto": "", "tipo": "audio", "midia": audio})
            self.assertIn("passei para uma pessoa do RH ouvir", self.ultima())
            self.postar({"de": CEL_NOVO_GATEWAY, "id": "a2", "texto": "", "tipo": "audio", "midia": audio})
        self.assertIn("não consigo ouvir áudios", self.ultima(CEL_NOVO_GATEWAY))  # fora do cadastro: não encaminha
        conversa = Conversa.objects.get(numero=CEL_CLARA)
        self.assertEqual(conversa.estado, Conversa.Estado.HUMANO)
        recebido = conversa.mensagens.get(texto="[áudio]")
        self.assertTrue(recebido.anexo_caminho.startswith("whatsapp/recebidos/audios/"))
        self.client.force_login(self.rh)
        self.assertContains(self.client.get(reverse("admin:whatsapp_atender", args=[conversa.pk])), "<audio controls")
        self.assertEqual(self.client.get(reverse("arquivo_privado_admin", args=[recebido.anexo_caminho])).status_code, 200)

    def test_conversa_com_o_rh_o_assistente_nao_atrapalha(self):
        audio = {"base64": base64.b64encode(b"OggS\x00\x02audio").decode(), "nome": "audio.ogg"}
        with mock.patch("whatsapp.canal.CanalGateway._pedido", return_value={"id": "r"}):
            self.postar({"de": CEL_CLARA, "id": "1", "texto": "quero falar com o rh"})
            antes = len(self.respostas())
            for n in range(15):  # conversa animada com o RH: várias mensagens curtas no mesmo minuto
                self.postar({"de": CEL_CLARA, "id": f"m{n}", "texto": f"mensagem {n}"})
            self.postar({"de": CEL_CLARA, "id": "a1", "texto": "", "tipo": "audio", "midia": audio})
            self.assertEqual(len(self.respostas()), antes)  # o robô não falou nada
            self.postar({"de": CEL_CLARA, "id": "l1", "texto": "", "tipo": "ligacao"})
            self.postar({"de": CEL_CLARA, "id": "l2", "texto": "", "tipo": "ligacao"})
        self.assertEqual(self.respostas()[antes:], [atendimento.LIGACAO_COM_O_RH])  # um aviso neutro só
        conversa = Conversa.objects.get(numero=CEL_CLARA)
        self.assertEqual(conversa.estado, Conversa.Estado.HUMANO)
        self.assertTrue(conversa.mensagens.filter(anexo_caminho__startswith="whatsapp/recebidos/audios/").exists())

    def test_rh_escrevendo_pelo_celular_para_o_assistente(self):
        with mock.patch("whatsapp.canal.CanalGateway._pedido", return_value={"id": "id-do-robo"}):
            self.postar({"de": CEL_CLARA, "id": "c1", "texto": "contracheque"})
            antes = len(self.respostas())
            self.postar({"de": CEL_CLARA, "id": "id-do-robo", "texto": "eco", "tipo": "rh_celular"})  # do próprio robô
            self.assertEqual(Conversa.objects.get(numero=CEL_CLARA).estado, Conversa.Estado.ATENDIMENTO)
            self.postar({"de": CEL_CLARA, "id": "rh1", "texto": "oi", "tipo": "rh_celular"})  # o RH, no celular
            self.postar({"de": CEL_CLARA, "id": "c2", "texto": "Oi"})  # o funcionário responde
        conversa = Conversa.objects.get(numero=CEL_CLARA)
        self.assertEqual(conversa.estado, Conversa.Estado.HUMANO)
        self.assertEqual(self.respostas()[antes:], ["oi"])  # só a mensagem do RH; o robô não entrou
        self.assertEqual(conversa.mensagens.get(id_externo="rh1").autor, Mensagem.Autor.RH)

    def test_volta_ao_assistente_se_o_rh_esqueceu_de_devolver(self):
        self.publicar_contracheques(1)
        self.comecar()
        self.falar("quero falar com o rh")
        conversa = Conversa.objects.get(numero=CEL_CLARA)
        self.falar("meu contracheque")
        self.assertEqual(Conversa.objects.get(pk=conversa.pk).estado, Conversa.Estado.HUMANO)  # esperando o RH
        Mensagem.objects.filter(conversa=conversa).update(criada_em=timezone.now() - timedelta(days=3))
        self.falar("meu contracheque")  # 3 dias e o RH ainda não respondeu: continua na fila
        self.assertEqual(Conversa.objects.get(pk=conversa.pk).estado, Conversa.Estado.HUMANO)
        Mensagem.objects.create(conversa=conversa, direcao="saida", autor="rh", texto="Resolvido!")
        Mensagem.objects.filter(conversa=conversa).update(criada_em=timezone.now() - timedelta(hours=25))
        Conversa.objects.filter(pk=conversa.pk).update(aguardando_humano_desde=timezone.now() - timedelta(days=3))
        self.falar("meu contracheque")  # o RH respondeu e a conversa parou: o assistente volta
        self.assertEqual(self.anexos(), ["contracheque_2026-01.pdf"])

    def anexos(self):
        return list(Mensagem.objects.exclude(anexo_nome="").values_list("anexo_nome", flat=True))

    def test_arquivo_recebido_chega_ao_atendimento(self):
        recebido = {}
        with mock.patch("whatsapp.views.receber", side_effect=lambda numero, **k: recebido.update(k, numero=numero)):
            self.postar({"de": "+55 98 98765-4321", "id": "m2", "texto": "",
                         "midia": {"base64": base64.b64encode(PDF).decode(), "nome": "termo.pdf"}})
        self.assertEqual(recebido["numero"], CEL_CLARA)
        self.assertEqual(recebido["midia"], {"conteudo": PDF, "nome": "termo.pdf"})

    def test_pagina_de_conexao_mostra_o_qr_code(self):
        self.client.force_login(self.rh)
        url = reverse("admin:whatsapp_conexao")
        with mock.patch("whatsapp.canal.CanalGateway._pedido",
                        return_value={"estado": "aguardando_qr", "qr": "data:image/png;base64,AAAA"}):
            self.assertContains(self.client.get(url), 'src="data:image/png;base64,AAAA"')
        with mock.patch("whatsapp.canal.CanalGateway._pedido", return_value={"estado": "conectado",
                                                                             "numero": CEL_CLARA}):
            self.assertContains(self.client.get(url), "5598*****4321")
        self.assertContains(self.client.get(url), "gateway está desligado")  # sem o gateway rodando


@override_settings(WHATSAPP_CANAL="meta")
class BuscaDeFuncionarioTestes(AssistenteBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.rh)
        self.url = reverse("admin:whatsapp_simulador")

    def test_busca_por_nome_matricula_cpf_ou_celular(self):
        from .admin import buscar_funcionarios
        for termo in ("clara santos", "7013", "529.982.247-25", "(98) 98765-4321"):
            self.assertEqual(list(buscar_funcionarios(termo)), [self.clara], termo)
        self.assertEqual(set(buscar_funcionarios("a")), {self.clara, self.joao})

    def test_um_resultado_abre_a_conversa_e_varios_mostram_a_lista(self):
        resposta = self.client.get(self.url, {"busca": "clara"})
        self.assertRedirects(resposta, f"{self.url}?numero={CEL_CLARA}", fetch_redirect_response=False)
        lista = self.client.get(self.url, {"busca": "a"})
        self.assertContains(lista, "CLARA MARIA SILVA SANTOS")
        self.assertContains(lista, "JOAO PEREIRA")
        self.assertContains(lista, "Abrir conversa")

    def test_lista_de_atendimentos_mostra_nome_e_matricula(self):
        self.falar("oi")
        resposta = self.client.get(reverse("admin:whatsapp_conversa_changelist"), {"q": "clara"})
        self.assertContains(resposta, "CLARA MARIA SILVA SANTOS")
        self.assertContains(resposta, "7013")


class MenuNumeradoTestes(AssistenteBase):
    def test_responde_so_com_o_numero(self):
        self.publicar_contracheques(1, 2)
        self.comecar()
        self.assertIn("1 - Contracheque", self.ultima())
        self.falar("1")
        self.assertIn("02 - Fevereiro", self.ultima())
        self.falar("2")
        self.assertEqual(list(Mensagem.objects.exclude(anexo_nome="").values_list("anexo_nome", flat=True)),
                         ["contracheque_2026-02.pdf"])

    def test_ok_encerra_sem_repetir_o_menu_e_sim_mostra_o_menu(self):
        self.publicar_contracheques(1)
        self.comecar()
        self.falar("meu contracheque")
        self.assertEqual(self.ultima(), "Posso ajudar em mais alguma coisa?")
        self.falar("Ok !")
        self.assertIn("Combinado", self.ultima())
        self.assertNotIn("Posso te ajudar com", self.ultima())
        total = len(self.respostas())
        self.falar("👍")  # despedida de novo: não fica num pingue-pongue
        self.assertEqual(len(self.respostas()), total)
        self.falar("sim")
        self.assertIn("1 - Contracheque", self.ultima())

    def test_nao_entendeu_mas_orienta_pelo_assunto(self):
        self.comecar()
        self.falar("vai cair hoje ??")
        self.assertIn("Se é sobre o seu pagamento", self.ultima())

    def test_vt_sozinho_mostra_o_menu_do_vt(self):
        self.dar_termo_vt()
        self.comecar()
        self.falar("Vt")
        self.assertIn("Sobre o vale-transporte", self.ultima())
        self.falar("2")
        self.assertIn("NÃO UTILIZAÇÃO", self.ultima())
        self.assertEqual(Conversa.objects.get().estado, Conversa.Estado.AGUARDANDO_TERMO)

    def test_numero_fora_do_menu(self):
        self.comecar()
        self.falar("9")
        self.assertIn("Escolha um número de 1 a 6", self.ultima())

    def test_opcao_duvida_desligada_e_rh_e_o_6(self):
        self.comecar()
        menu = self.ultima()
        self.assertNotIn("Tirar uma dúvida", menu)
        self.assertIn("6 - Falar com uma pessoa do RH", menu)
        self.falar("vai cair hoje ??")
        self.assertIn("responda *6* e uma pessoa do RH", self.ultima())
        self.falar("6")
        self.assertIn("Vou passar a sua mensagem para uma pessoa do RH", self.ultima())

    def test_opcao_duvida_ligada_pelo_rh(self):
        config = ConfiguracaoPortal.atual()
        config.whatsapp_opcao_duvidas = True
        config.save()
        self.comecar()
        self.assertIn("6 - Tirar uma dúvida", self.ultima())
        self.assertIn("7 - Falar com uma pessoa do RH", self.ultima())
        self.falar("6")
        self.assertIn("escrever a sua dúvida", self.ultima())
        self.falar("ticket")
        self.assertIn("responda *6* e escreva a sua dúvida, ou responda *7*", self.ultima())


CEL_NOVO = "5598911112222"


class CadastroPeloWhatsappTestes(AssistenteBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.rh)

    def pedir_acesso(self, nome="Joao Pereira", cpf="111.444.777-35", numero=CEL_NOVO):
        self.falar("oi", numero=numero)
        self.falar("1", numero=numero)  # "sou funcionário"
        self.falar(nome, numero=numero)
        self.falar(cpf, numero=numero)
        return PedidoCadastro.objects.get()

    def test_quem_procura_vaga_nao_vira_cadastro_pendente(self):
        self.falar("Bom dia tudo bem", numero=CEL_NOVO)
        self.assertIn("1 - Funcionário do Grupo Global", self.ultima(CEL_NOVO))
        self.falar("2", numero=CEL_NOVO)
        self.assertIn("não recebe currículos", self.ultima(CEL_NOVO))  # sem texto na Configuração
        config = ConfiguracaoPortal.atual()
        config.whatsapp_texto_curriculo = "Envie o seu currículo para rh@empresa.com.br"
        config.save()
        outro = "5598977776666"
        self.falar("Gostaria de saber se tem alguma vaga de porteiro", numero=outro)  # já chega falando de vaga
        self.assertEqual(self.ultima(outro), "Envie o seu currículo para rh@empresa.com.br")
        self.falar(midia={"conteudo": PDF, "nome": "curriculo.pdf"}, numero="5598955554444")  # manda o PDF direto
        self.assertIn("rh@empresa.com.br", self.ultima("5598955554444"))
        self.falar("Igor Pereira da Silva", numero=CEL_NOVO)  # escreveu outra coisa: lembra as opções
        self.assertIn("responda com o número", self.ultima(CEL_NOVO))
        self.assertFalse(PedidoCadastro.objects.exists())
        self.assertFalse(Mensagem.objects.exclude(anexo_caminho="").exists())  # o currículo não fica guardado

    def test_outro_assunto_vai_para_o_rh_e_contracheque_ja_pede_o_nome(self):
        self.falar("oi", numero=CEL_NOVO)
        self.falar("3", numero=CEL_NOVO)
        self.assertIn("uma pessoa do RH te responde", self.ultima(CEL_NOVO))
        self.assertEqual(Conversa.objects.get(numero=CEL_NOVO).estado, Conversa.Estado.HUMANO)
        self.falar("quero meu contracheque", numero="5598955554444")  # funcionário que trocou de número
        self.assertIn("me diga o seu nome completo", self.ultima("5598955554444"))
        self.client.force_login(self.rh)
        conversa = Conversa.objects.get(numero=CEL_NOVO)
        self.assertEqual(self.client.get(reverse("admin:whatsapp_atender", args=[conversa.pk])).status_code, 200)

    def test_pede_nome_e_cpf_e_manda_para_o_rh(self):
        self.falar("oi", numero=CEL_NOVO)
        self.falar("1", numero=CEL_NOVO)
        self.falar("123", numero=CEL_NOVO)
        self.assertIn("nome completo", self.ultima(CEL_NOVO))  # nome inválido: pede de novo
        self.falar("Bom dia", numero=CEL_NOVO)
        self.assertIn("nome completo", self.ultima(CEL_NOVO))  # saudação não é nome
        self.falar("Joao Pereira", numero=CEL_NOVO)
        self.assertIn("CPF", self.ultima(CEL_NOVO))
        self.falar("123.456.789-00", numero=CEL_NOVO)
        self.assertIn("não parece certo", self.ultima(CEL_NOVO))  # dígitos não conferem: pede de novo
        self.falar("não sei", numero=CEL_NOVO)
        self.assertIn("gov.br", self.ultima(CEL_NOVO))
        self.assertFalse(PedidoCadastro.objects.exists())
        self.falar("111.444.777-35", numero=CEL_NOVO)
        pedido = PedidoCadastro.objects.get()
        self.assertEqual((pedido.nome_informado, pedido.cpf_informado, pedido.situacao),
                         ("Joao Pereira", "11144477735", "pendente"))
        self.assertIn("Enviei os seus dados para o RH", self.ultima(CEL_NOVO))
        # LGPD: o CPF digitado fica mascarado no histórico da conversa
        textos = list(Mensagem.objects.filter(direcao="entrada").values_list("texto", flat=True))
        self.assertIn("111.***.***-35", textos)
        self.assertFalse(any("444" in t for t in textos))
        self.falar("quero meu contracheque", numero=CEL_NOVO)
        self.assertIn("Anotei a sua mensagem", self.ultima(CEL_NOVO))
        total = len(self.respostas(CEL_NOVO))
        self.falar("?", numero=CEL_NOVO)  # continua escrevendo: não repete a mesma frase toda vez
        self.assertEqual(len(self.respostas(CEL_NOVO)), total)
        self.assertFalse(Mensagem.objects.exclude(anexo_nome="").exists())  # nada é entregue antes do RH

    def test_nao_libera_para_outro_funcionario_mesmo_com_nome_parecido(self):
        # Caso que já aconteceu: alguém que não é funcionário foi liberado por engano para um funcionário de nome parecido.
        pedido = self.pedir_acesso(nome="Joao Silva Pereira", cpf="529.982.247-25")  # CPF da Clara, não do João
        url = reverse("admin:whatsapp_pedidocadastro_analisar", args=[pedido.pk])
        tela = self.client.get(url)
        self.assertContains(tela, "529.***.***-25")
        self.assertNotContains(tela, "529.982.247-25")
        self.client.post(url, {"funcionario": self.joao.pk})  # mesmo forçando, o sistema recusa
        self.joao.refresh_from_db()
        self.assertEqual((self.joao.whatsapp, PedidoCadastro.objects.get().situacao), ("5598981234567", "pendente"))
        self.assertFalse(Mensagem.objects.filter(conversa__numero=CEL_NOVO, texto__contains="liberou").exists())

    def test_pedido_antigo_sem_cpf_pede_o_cpf(self):
        conversa = Conversa.objects.create(numero=CEL_NOVO, simulada=True)
        pedido = PedidoCadastro.objects.create(conversa=conversa, numero=CEL_NOVO, nome_informado="Joao Pereira")
        url = reverse("admin:whatsapp_pedidocadastro_analisar", args=[pedido.pk])
        self.assertContains(self.client.get(url), "antes da conferência por CPF")
        self.client.post(url, {"funcionario": self.joao.pk})
        self.assertEqual(PedidoCadastro.objects.get().situacao, "pendente")  # sem CPF não libera
        self.falar("oi", numero=CEL_NOVO)
        self.assertIn("CPF", self.ultima(CEL_NOVO))
        self.falar("11144477735", numero=CEL_NOVO)
        self.assertEqual(PedidoCadastro.objects.get().cpf_informado, "11144477735")
        self.client.post(url, {"funcionario": self.joao.pk})
        self.assertEqual(PedidoCadastro.objects.get().situacao, "liberado")

    def test_libera_numero_que_o_whatsapp_manda_sem_o_nove(self):
        sem_nove = "559881112222"  # o WhatsApp informa celulares antigos sem o 9 (o caso do Wesclen)
        pedido = self.pedir_acesso(numero=sem_nove)
        self.client.post(reverse("admin:whatsapp_pedidocadastro_analisar", args=[pedido.pk]), {"funcionario": self.joao.pk})
        self.joao.refresh_from_db()
        self.assertEqual((self.joao.telefone, self.joao.whatsapp), ("(98) 98111-2222", "5598981112222"))  # antes: (59)
        self.falar("1", numero=sem_nove)
        self.assertNotIn("não encontrei este número", self.ultima(sem_nove).lower())
        self.assertIn("contracheque", self.ultima(sem_nove).lower())

    def test_rh_libera_e_a_pessoa_e_avisada(self):
        pedido = self.pedir_acesso()
        url = reverse("admin:whatsapp_pedidocadastro_analisar", args=[pedido.pk])
        tela = self.client.get(url)
        self.assertContains(tela, "JOAO PEREIRA")
        self.assertContains(tela, "✅ CPF confere")
        self.assertContains(tela, "será trocado por")  # o João já tinha outro celular
        self.client.post(url, {"funcionario": self.joao.pk})
        self.joao.refresh_from_db()
        self.assertEqual(self.joao.whatsapp, CEL_NOVO)
        self.assertEqual(PedidoCadastro.objects.get().situacao, "liberado")
        self.assertIn("liberou o seu acesso ao Assistente do Colaborador", self.ultima(CEL_NOVO))
        self.assertTrue(LogAuditoria.objects.filter(acao="whatsapp_cadastro_liberado").exists())
        self.falar("1", numero=CEL_NOVO)  # já entra no menu
        self.assertIn("contracheque", self.ultima(CEL_NOVO).lower())

    def test_nao_libera_celular_que_ja_e_de_outra_pessoa(self):
        self.joao.telefone = "(98) 91111-2222"
        self.joao.save()
        pedido = PedidoCadastro.objects.create(conversa=Conversa.objects.create(numero="5598933334444"),
                                               numero=CEL_CLARA, nome_informado="Joao Pereira",
                                               cpf_informado="11144477735")
        self.client.post(reverse("admin:whatsapp_pedidocadastro_analisar", args=[pedido.pk]),
                         {"funcionario": self.joao.pk})
        self.assertEqual(PedidoCadastro.objects.get().situacao, "pendente")
        self.joao.refresh_from_db()
        self.assertEqual(self.joao.whatsapp, "5598911112222")

    def test_rh_recusa(self):
        pedido = self.pedir_acesso(nome="Fulano de Tal", cpf="01234567890")
        self.client.post(reverse("admin:whatsapp_pedidocadastro_analisar", args=[pedido.pk]), {"recusar": "1"})
        self.assertEqual(PedidoCadastro.objects.get().situacao, "recusado")
        self.assertIn("Procure o seu supervisor", self.ultima(CEL_NOVO))
        self.falar("oi", numero=CEL_NOVO)
        self.assertIn("Procure o seu supervisor", self.ultima(CEL_NOVO))

    def test_menu_mostra_os_pendentes(self):
        self.pedir_acesso()
        resposta = self.client.get(reverse("admin:index"))
        self.assertContains(resposta, "Cadastros pendentes")
        self.assertContains(resposta, 'class="pn-numero"')


class FardamentoTestes(AssistenteBase):
    def setUp(self):
        super().setUp()
        self.comecar()

    def itens(self):
        return sorted(ItemFardamento.objects.values_list("peca", "descricao", "tamanho"))

    def test_pede_pecas_tamanhos_e_motivo_pelo_menu(self):
        self.falar("4")
        self.assertIn("1 - Camisa", self.ultima())
        self.falar("1 e 3")
        self.assertIn("tamanho da *camisa*", self.ultima())
        self.falar("tamanho m")
        self.assertIn("número da *bota / sapato*", self.ultima())
        self.falar("XL")
        self.assertIn("Não entendi o tamanho", self.ultima())  # bota é número
        self.falar("41")
        self.assertIn("Por que você precisa", self.ultima())
        self.falar("a camisa rasgou e a bota furou")
        pedido = Solicitacao.objects.get()
        self.assertEqual((pedido.tipo.codigo, pedido.status, pedido.funcionario), ("fardamento", "pendente", self.clara))
        self.assertEqual(self.itens(), [("bota", "", "41"), ("camisa", "", "M")])
        self.assertIn(pedido.protocolo, self.ultima())
        self.assertIn("• Camisa: M", self.ultima())
        self.falar("quero uma calça")  # já tem um pedido sem resposta: não abre outro
        self.assertIn("já tem um pedido de fardamento em andamento", self.ultima())
        self.assertEqual(Solicitacao.objects.count(), 1)

    def test_escreveu_a_peca_e_outra_peca(self):
        self.falar("preciso de uma bota nova")
        self.assertIn("número da *bota / sapato*", self.ultima())  # já sabe a peça
        self.falar("cancelar")
        self.assertIn("cancelado", self.ultima())
        self.falar("fardamento")
        self.falar("4")
        self.assertIn("Qual é a outra peça", self.ultima())
        self.falar("jaqueta")
        self.assertIn("tamanho de *jaqueta*", self.ultima())
        self.falar("g")
        self.falar("posto novo")
        self.assertEqual(self.itens(), [("outra", "jaqueta", "G")])

    def test_relatorio_do_rh_com_resumo_e_planilha(self):
        for tamanho in ("M", "G", "M"):
            for funcionario in (self.clara,):
                tipo = tipo_fardamento_disponivel(funcionario)
                pedido = criar_solicitacao_fardamento(funcionario, tipo, [("camisa", "", tamanho), ("bota", "", "40")],
                                                      "desgastou")
                pedido.status = Solicitacao.Status.CONCLUIDA
                pedido.save()
        self.client.force_login(self.rh)
        tela = self.client.get(reverse("admin:solicitacoes_itemfardamento_changelist"))
        self.assertContains(tela, "Resumo para compra (6 peças)")
        self.assertContains(tela, "CLARA MARIA SILVA SANTOS")
        self.assertContains(tela, "desgastou")
        filtrada = self.client.get(reverse("admin:solicitacoes_itemfardamento_changelist") + "?peca__exact=camisa")
        self.assertContains(filtrada, "Resumo para compra (3 peças)")
        planilha = self.client.get(reverse("admin:solicitacoes_itemfardamento_exportar") + "?peca__exact=camisa")
        self.assertEqual(planilha.status_code, 200)
        self.assertIn("spreadsheetml", planilha["Content-Type"])
        livro = load_workbook(io.BytesIO(planilha.content))
        linhas = list(livro.active.values)
        self.assertEqual(linhas[0][:3], ("Pedido em", "Protocolo", "Funcionário"))
        self.assertEqual(sorted(l[8] for l in linhas[1:]), ["G", "M", "M"])
        pedido = Solicitacao.objects.first()
        tela_pedido = self.client.get(reverse("admin:solicitacoes_solicitacao_change", args=[pedido.pk]))
        self.assertContains(tela_pedido, "Peças de fardamento pedidas")
        self.assertContains(tela_pedido, "Bota / sapato")
