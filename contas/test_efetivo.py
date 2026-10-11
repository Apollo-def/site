import io
from datetime import date

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from openpyxl import Workbook

from .importacao_efetivo import atualizar_pelo_efetivo
from .models import Contrato, Funcionario
from .validadores import extrair_celular, formatar_celular

TITULOS = ["Funcionário", "Matrícula", "Tipo de Posto", "Fones", "Instalação"]


def planilha(linhas):
    livro = Workbook()
    aba = livro.active
    aba.append(TITULOS)
    for linha in linhas:
        aba.append(linha)
    saida = io.BytesIO()
    livro.save(saida)
    saida.seek(0)
    return saida


class ExtrairCelularTestes(SimpleTestCase):
    def test_formatos_da_planilha(self):
        for valor in ("98987654321", 98987654321, "98 987654321", "98-987654321", "(98) 98765-4321",
                      "098 98765-4321", "+55 98 98765-4321", "98 98765-4321 / 98 3232-1010",
                      "98 3232-1010 / 98 98765-4321", "9898765432198999991111"):
            self.assertEqual(extrair_celular(valor), "5598987654321", valor)

    def test_recusa_sem_ddd_fixo_e_vazio(self):
        for valor in ("987654321", "98765-4321", "98 3232-1010", "", None, "sem telefone"):
            self.assertEqual(extrair_celular(valor), "", valor)

    def test_formatar(self):
        self.assertEqual(formatar_celular("5598987654321"), "(98) 98765-4321")


class AtualizarPeloEfetivoTestes(TestCase):
    def setUp(self):
        semed = Contrato.objects.create(sigla="SEMED")
        dados = dict(data_nascimento=date(1990, 1, 1), contrato=semed)
        self.ana = Funcionario.objects.create(nome_completo="ANA MARIA SOUSA", matricula="4830", cpf="52998224725",
                                              **dados)
        self.bia = Funcionario.objects.create(nome_completo="BEATRIZ LIMA", matricula="4831", cpf="11144477735",
                                              telefone="(98) 99111-2222", **dados)
        self.caio = Funcionario.objects.create(nome_completo="CAIO REIS", matricula="4832", cpf="01234567890", **dados)
        self.davi = Funcionario.objects.create(nome_completo="DAVI COSTA", matricula="4833", cpf="12345678909", **dados)

    def enviar(self, linhas, simulacao=False):
        return atualizar_pelo_efetivo(planilha(linhas), "Efetivo.xlsx", simulacao=simulacao)

    def test_atualiza_celular_posto_e_instalacao_pela_matricula(self):
        r = self.enviar([["Ana Maria Sousa", 4830, "PORTEIRO - MA", "98 98765-4321", "UEB ESCOLA X"]])
        self.assertEqual((len(r.atualizados), r.total_linhas), (1, 1))
        self.ana.refresh_from_db()
        self.assertEqual((self.ana.telefone, self.ana.whatsapp), ("(98) 98765-4321", "5598987654321"))
        self.assertEqual((self.ana.cargo, self.ana.instalacao), ("PORTEIRO - MA", "UEB ESCOLA X"))
        self.assertEqual(self.ana.cpf, "52998224725")  # CPF não é mexido

    def test_simulacao_nao_salva(self):
        r = self.enviar([["Ana Maria Sousa", 4830, "PORTEIRO - MA", "98987654321", "UEB X"]], simulacao=True)
        self.assertEqual(len(r.atualizados), 1)
        self.ana.refresh_from_db()
        self.assertEqual((self.ana.telefone, self.ana.instalacao), ("", ""))

    def test_nome_que_nao_confere_e_recusado(self):
        r = self.enviar([["PEDRO ALVES", 4830, "PORTEIRO - MA", "98987654321", "UEB X"]])
        self.assertEqual(len(r.nome_diferente), 1)
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.telefone, "")

    def test_telefone_invalido_nao_apaga_o_que_ja_existia(self):
        r = self.enviar([["Beatriz Lima", 4831, "PORTEIRO - MA", "991112222", "UEB Y"],
                         ["Caio Reis", 4832, "PORTEIRO - MA", None, "UEB Z"]])
        self.assertEqual((len(r.telefone_invalido), len(r.sem_telefone)), (1, 1))
        self.bia.refresh_from_db()
        self.assertEqual((self.bia.telefone, self.bia.instalacao), ("(98) 99111-2222", "UEB Y"))

    def test_aponta_matricula_fora_do_cadastro_e_celular_repetido(self):
        r = self.enviar([["Caio Reis", 4832, "PORTEIRO - MA", "98 98888-7777", "UEB Z"],
                         ["Davi Costa", 4833, "PORTEIRO - MA", "98988887777", "UEB Z"],
                         ["Fulano", 9999, "PORTEIRO - MA", "98 97777-6666", "UEB W"]])
        self.assertEqual(len(r.nao_encontrados), 1)
        self.assertEqual(len(r.repetidos), 1)
        celular, nomes = r.repetidos[0]
        self.assertEqual(celular, "(98) 98888-7777")
        self.assertEqual(nomes, ["CAIO REIS (4832)", "DAVI COSTA (4833)"])
        self.assertEqual(r.ativos_fora_da_planilha, 2)  # Ana e Beatriz não estavam nesta planilha

    def test_tela_do_painel(self):
        rh = get_user_model().objects.create_superuser("rh", "rh@x.com", "SenhaForte!2026")
        self.client.force_login(rh)
        url = reverse("admin:contas_funcionario_atualizar_efetivo")
        self.assertContains(self.client.get(reverse("admin:contas_funcionario_changelist")), url)
        arquivo = SimpleUploadedFile("Efetivo.xlsx", planilha([["Ana Maria Sousa", 4830, "PORTEIRO - MA",
                                                                 "98987654321", "UEB X"]]).read())
        resposta = self.client.post(url, {"arquivo": arquivo})
        self.assertContains(resposta, "Cadastro atualizado")
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.whatsapp, "5598987654321")
