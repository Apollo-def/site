import io

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from .importacao import corrigir_acentos, importar_funcionarios
from .models import Funcionario, Instalacao, cpf_valido

CPF_A = "52998224725"
CPF_B = "11144477735"


def planilha(*linhas, titulos=("Nome completo", "Matrícula", "CPF", "Instalação", "Cargo", "Telefone", "Escala")):
    livro = Workbook()
    aba = livro.active
    aba.append(list(titulos))
    for linha in linhas:
        aba.append(list(linha))
    saida = io.BytesIO()
    livro.save(saida)
    saida.seek(0)
    return saida


class ValidacaoTests(TestCase):
    def test_cpf(self):
        self.assertTrue(cpf_valido(CPF_A))
        self.assertFalse(cpf_valido("52998224724"))
        self.assertFalse(cpf_valido("11111111111"))

    def test_corrige_acento_quebrado(self):
        self.assertEqual(corrigir_acentos("SAO LUÃ\x8dS"), "SAO LUÍS")
        self.assertEqual(corrigir_acentos("SÃO LUÍS"), "SÃO LUÍS")


class ImportacaoTests(TestCase):
    def test_cadastra_e_cria_instalacao(self):
        r = importar_funcionarios(planilha(
            ["MARIA SILVA", 101, int(CPF_A), "FORUM SAO LUÃ\x8dS", "COPEIRA(O)", None, "DRTA"],
            ["JOAO SOUZA", 102, CPF_B, "FORUM SAO LUÃ\x8dS", "CONTINUO", None, "DRTA-S"],
        ))
        self.assertEqual((r.criados, r.erros), (2, []))
        self.assertEqual(Instalacao.objects.get().nome, "FORUM SAO LUÍS")
        maria = Funcionario.objects.get(matricula="101")
        self.assertEqual((maria.cpf, maria.escala, maria.data_nascimento), (CPF_A, "DRTA", None))

    def test_cpf_sem_zero_a_esquerda(self):
        cpf = "04512345612"  # válido, começa com zero
        self.assertTrue(cpf_valido(cpf))
        importar_funcionarios(planilha(["ANA", 1, int(cpf), "", "", None, ""]))
        self.assertEqual(Funcionario.objects.get().cpf, cpf)

    def test_atualiza_pela_matricula_sem_apagar_campos(self):
        importar_funcionarios(planilha(["ANA", 1, CPF_A, "FORUM", "COPEIRA(O)", None, "DRTA"]))
        r = importar_funcionarios(planilha(["ANA", 1, CPF_A, "FORUM", "ENCARREGADO", None, ""]))
        self.assertEqual(r.atualizados, 1)
        ana = Funcionario.objects.get()
        self.assertEqual((ana.cargo, ana.escala), ("ENCARREGADO", "DRTA"))
        r = importar_funcionarios(planilha(["ANA", 1, CPF_A, "FORUM", "ENCARREGADO", None, ""]))
        self.assertEqual(r.sem_alteracao, 1)

    def test_erros_por_linha(self):
        r = importar_funcionarios(planilha(
            ["ANA", 1, CPF_A, "", "", None, ""],
            ["BIA", 2, "12345678900", "", "", None, ""],
            ["CAIO", 1, CPF_B, "", "", None, ""],
            ["DANI", 3, CPF_A, "", "", None, ""],
        ))
        self.assertEqual(r.criados, 1)
        self.assertEqual([linha for linha, _, _ in r.erros], [3, 4, 5])

    def test_instalacao_nao_fica_orfa_quando_linha_falha(self):
        r = importar_funcionarios(planilha(
            ["BIA", 2, "12345678900", "PREDIO NOVO", "", None, ""],
            ["ANA", 1, CPF_A, "PREDIO NOVO", "", None, ""],
        ))
        self.assertEqual((r.criados, len(r.erros)), (1, 1))
        self.assertEqual(Funcionario.objects.get().instalacao.nome, "PREDIO NOVO")

    def test_somente_conferir_nao_salva(self):
        r = importar_funcionarios(planilha(["ANA", 1, CPF_A, "FORUM", "", None, ""]), simulacao=True)
        self.assertEqual(r.criados, 1)
        self.assertFalse(Funcionario.objects.exists())
        self.assertFalse(Instalacao.objects.exists())

    def test_sem_colunas_obrigatorias(self):
        r = importar_funcionarios(planilha(["ANA", 1], titulos=("Nome completo", "Matrícula")))
        self.assertIn("CPF", r.erro_geral)


class TelaImportacaoTests(TestCase):
    def test_rh_importa_pelo_painel(self):
        self.client.force_login(get_user_model().objects.create_superuser("rh", "rh@x.com", "senha-forte-123"))
        url = reverse("admin:funcionarios_importar")
        self.assertEqual(self.client.get(url).status_code, 200)
        arquivo = planilha(["ANA", 1, CPF_A, "FORUM", "", None, "DRTA"])
        arquivo.name = "funcionarios.xlsx"
        resposta = self.client.post(url, {"arquivo": arquivo})
        self.assertContains(resposta, "1 cadastrados")
        self.assertTrue(Funcionario.objects.filter(matricula="1").exists())
