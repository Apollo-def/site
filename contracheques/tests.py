import io
import shutil
import tempfile
from datetime import date

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from auditoria.models import LogAuditoria
from contas.models import Contrato, Funcionario

from portal.lotes import nomes_compativeis

from .extracao import ler_pagina
from .models import Contracheque, LoteContracheque, PaginaNaoIdentificada
from .processamento import processar_lote

PASTA_TESTE = tempfile.mkdtemp(prefix="portal_testes_")


def pagina_contracheque(matricula, nome, competencia="Janeiro de 2026"):
    return ["GLOBALTECH BRASIL LTDA", "CNPJ: 00.000.000/0001-00 CC: GERAL Folha Mensal", f"Mensalista {competencia}",
            "Código Nome do Funcionário CBO Departamento Filial", f"{matricula} {nome} 422105 001 1",
            "PORTEIRO Admissão: 01/01/2020", "Código Descrição Referência Vencimentos Descontos",
            "0001 DIAS NORMAIS 30,00 1.500,00"]


def gerar_pdf(paginas):
    saida = io.BytesIO()
    folha = canvas.Canvas(saida, pagesize=letter)
    for linhas in paginas:
        y = 740
        for linha in linhas:
            folha.drawString(40, y, linha)
            y -= 16
        folha.showPage()
    folha.save()
    return saida.getvalue()


class ExtracaoTestes(TestCase):
    def test_le_matricula_nome_e_competencia(self):
        dados = ler_pagina("\n".join(pagina_contracheque("7013", "CLARA MARIA SILVA SANTOS")))
        self.assertEqual(dados.matriculas, ["7013"])
        self.assertEqual(dados.nomes["7013"], "CLARA MARIA SILVA SANTOS")
        self.assertEqual((dados.mes, dados.ano, dados.tipo), (1, 2026, "mensal"))

    def test_pagina_sem_texto(self):
        self.assertFalse(ler_pagina("").tem_texto)

    def test_conferencia_de_nome(self):
        self.assertTrue(nomes_compativeis("CLARA MARIA S SANTOS", "Clara Maria Silva Santos"))
        self.assertTrue(nomes_compativeis("JOSE CONCEICAO", "José Conceição"))
        self.assertFalse(nomes_compativeis("PEDRO ALVES", "Clara Maria Santos"))


@override_settings(PRIVATE_MEDIA_ROOT=PASTA_TESTE, PROCESSAR_EM_SEGUNDO_PLANO=False,
                   STORAGES={"default": {"BACKEND": "portal.armazenamento.ArmazenamentoPrivado"},
                             "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class Base(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(PASTA_TESTE, ignore_errors=True)

    def setUp(self):
        cache.clear()
        semed = Contrato.objects.create(sigla="SEMED")
        self.clara = Funcionario.objects.create(nome_completo="CLARA MARIA SILVA SANTOS", matricula="7013",
                                                cpf="52998224725", data_nascimento=date(1990, 3, 7),
                                                contrato=semed)
        self.joao = Funcionario.objects.create(nome_completo="JOAO PEREIRA", matricula="6068",
                                               cpf="11144477735", data_nascimento=date(1985, 8, 15), contrato=semed)
        self.rh = get_user_model().objects.create_superuser("rh", "rh@x.com", "SenhaForte!2026")

    def enviar(self, conteudo, mes=1, ano=2026, substituir=False):
        self.client.force_login(self.rh)
        dados = {"arquivo": SimpleUploadedFile("folha.pdf", conteudo, content_type="application/pdf"),
                 "mes": mes, "ano": ano, "tipo": "mensal"}
        if substituir:
            dados["substituir"] = "on"
        with self.captureOnCommitCallbacks(execute=True):
            resposta = self.client.post(reverse("admin:contracheques_lote_enviar"), dados)
        return resposta


class ProcessamentoTestes(Base):
    def test_separa_agrupa_e_lista_pendencias(self):
        pdf = gerar_pdf([
            pagina_contracheque("7013", "CLARA MARIA SILVA SANTOS"),
            pagina_contracheque("7013", "CLARA MARIA SILVA SANTOS"),   # 2ª página da mesma pessoa
            pagina_contracheque("6068", "JOAO PEREIRA"),
            pagina_contracheque("9999", "FULANO NAO CADASTRADO"),
            ["RESUMO GERAL DA FOLHA - TOTAIS POR DEPARTAMENTO E EVENTOS"],  # sem matrícula
        ])
        resposta = self.enviar(pdf)
        lote = LoteContracheque.objects.get()
        self.assertRedirects(resposta, reverse("admin:contracheques_lote_relatorio", args=[lote.pk]))
        self.assertEqual(lote.status, "concluido", lote.mensagem_erro)
        self.assertEqual((lote.total_paginas, lote.total_distribuidos, lote.total_nao_encontrados,
                          lote.total_sem_matricula), (5, 2, 1, 1))
        self.assertEqual(lote.competencia_detectada, "Janeiro de 2026")

        cc_clara = Contracheque.objects.get(funcionario=self.clara)
        self.assertEqual(cc_clara.paginas, "1-2")
        with cc_clara.arquivo.open("rb") as arquivo:
            self.assertEqual(len(PdfReader(arquivo).pages), 2)

        motivos = set(PaginaNaoIdentificada.objects.values_list("motivo", flat=True))
        self.assertEqual(motivos, {"nao_cadastrada", "sem_matricula"})
        relatorio = self.client.get(reverse("admin:contracheques_lote_relatorio", args=[lote.pk]))
        self.assertContains(relatorio, "FULANO NAO CADASTRADO")
        self.assertTrue(LogAuditoria.objects.filter(acao="upload_contracheques").exists())

    def test_nome_muito_diferente_nao_e_entregue(self):
        self.enviar(gerar_pdf([pagina_contracheque("7013", "PEDRO ALVES")]))
        self.assertFalse(Contracheque.objects.exists())
        self.assertEqual(PaginaNaoIdentificada.objects.get().motivo, "nome_divergente")

    def test_vincular_manualmente(self):
        self.enviar(gerar_pdf([pagina_contracheque("9999", "FULANO")]))
        pendente = PaginaNaoIdentificada.objects.get()
        self.client.post(reverse("admin:contracheques_pendente_vincular", args=[pendente.pk]),
                         {"identificador": "6068"})
        pendente.refresh_from_db()
        self.assertTrue(pendente.resolvida)
        cc = Contracheque.objects.get(funcionario=self.joao)
        self.assertTrue(cc.vinculado_manualmente)
        self.assertEqual(LoteContracheque.objects.get().total_distribuidos, 1)

    def test_descartar_pendencia(self):
        self.enviar(gerar_pdf([["CAPA DA FOLHA DE PAGAMENTO - JANEIRO"]]))
        pendente = PaginaNaoIdentificada.objects.get()
        self.client.post(reverse("admin:contracheques_pendente_descartar", args=[pendente.pk]))
        pendente.refresh_from_db()
        self.assertTrue(pendente.resolvida)
        self.assertIsNone(pendente.funcionario)

    def test_mesma_competencia_pede_confirmacao_e_substitui(self):
        self.enviar(gerar_pdf([pagina_contracheque("7013", "CLARA MARIA SILVA SANTOS")]))
        primeiro = Contracheque.objects.get()
        resposta = self.enviar(gerar_pdf([pagina_contracheque("6068", "JOAO PEREIRA")]))
        self.assertContains(resposta, "Deseja substituir?")
        self.assertEqual(LoteContracheque.objects.count(), 1)

        self.enviar(gerar_pdf([pagina_contracheque("6068", "JOAO PEREIRA")]), substituir=True)
        self.assertFalse(Contracheque.objects.filter(pk=primeiro.pk).exists())
        self.assertEqual(list(Contracheque.objects.values_list("funcionario__matricula", flat=True)), ["6068"])
        self.assertEqual(LoteContracheque.objects.filter(status="substituido").count(), 1)

    def test_avisa_competencia_diferente(self):
        self.enviar(gerar_pdf([pagina_contracheque("7013", "CLARA MARIA SILVA SANTOS")]), mes=2)
        self.assertIn("Janeiro de 2026", LoteContracheque.objects.get().avisos)

    def test_arquivo_que_nao_e_pdf(self):
        self.client.force_login(self.rh)
        resposta = self.client.post(reverse("admin:contracheques_lote_enviar"), {
            "arquivo": SimpleUploadedFile("folha.pdf", b"isto nao e um pdf"), "mes": 1, "ano": 2026, "tipo": "mensal"})
        self.assertContains(resposta, "não é um PDF válido")

    def test_pdf_corrompido_vira_erro(self):
        lote = LoteContracheque(ano=2026, mes=1)
        lote.arquivo.save("x.pdf", io.BytesIO(b"%PDF-1.4 quebrado"), save=True)
        processar_lote(lote.pk)
        lote.refresh_from_db()
        self.assertEqual(lote.status, "erro")


class ReentregaTestes(Base):
    def test_entrega_pendencias_depois_do_cadastro(self):
        self.enviar(gerar_pdf([pagina_contracheque("9999", "ANA NOVA LIMA"),
                               pagina_contracheque("8888", "BRUNO SEM CADASTRO")]))
        lote = LoteContracheque.objects.get()
        self.assertEqual(lote.total_nao_encontrados, 2)
        Funcionario.objects.create(nome_completo="Ana Nova Lima", matricula="9999", cpf="01234567890",
                                   data_nascimento=date(1995, 5, 5), contrato=self.clara.contrato)
        resposta = self.client.post(reverse("admin:contracheques_lote_reentregar", args=[lote.pk]), follow=True)
        self.assertContains(resposta, "1 contracheque(s) entregue(s)")
        self.assertTrue(Contracheque.objects.filter(funcionario__matricula="9999").exists())
        lote.refresh_from_db()
        self.assertEqual((lote.total_distribuidos, lote.total_nao_encontrados), (1, 1))
        self.assertEqual(lote.paginas_nao_identificadas.filter(resolvida=False).count(), 1)
