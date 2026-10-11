import os
from datetime import date
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import TestCase

from contas.models import Contrato, Funcionario
from contas.validadores import cpf_e_valido
from orientacoes.models import Anexo, Categoria, Orientacao
from solicitacoes.models import Solicitacao, TipoSolicitacao


class CPFTestes(TestCase):
    def test_cpfs_validos_com_e_sem_pontuacao(self):
        self.assertTrue(cpf_e_valido("529.982.247-25"))
        self.assertTrue(cpf_e_valido("11144477735"))

    def test_cpfs_invalidos(self):
        for cpf in ["529.982.247-26", "111.111.111-11", "123", ""]:
            self.assertFalse(cpf_e_valido(cpf), cpf)


class FuncionarioTestes(TestCase):
    def setUp(self):
        self.contrato = Contrato.objects.create(sigla="SEMED")

    def criar_funcionario(self, **extra):
        dados = dict(nome_completo="  Maria  da Silva ", matricula=" 1001 ", cpf="529.982.247-25",
                     data_nascimento=date(1990, 3, 7), contrato=self.contrato, cargo="Porteira")
        dados.update(extra)
        funcionario = Funcionario(**dados)
        funcionario.full_clean()
        funcionario.save()
        return funcionario

    def test_normaliza_dados(self):
        f = self.criar_funcionario()
        self.assertEqual(f.cpf, "52998224725")
        self.assertEqual(f.matricula, "1001")
        self.assertEqual(f.nome_completo, "Maria da Silva")
        self.assertEqual(f.cpf_formatado, "529.982.247-25")
        # busca por CPF com pontuação também funciona
        self.assertTrue(Funcionario.objects.filter(cpf="529.982.247-25").exists())

    def test_cpf_invalido_e_rejeitado(self):
        with self.assertRaises(ValidationError):
            self.criar_funcionario(cpf="529.982.247-26")

    def test_funcionario_nao_ganha_login_e_celular_vira_formato_do_whatsapp(self):
        f = self.criar_funcionario(telefone="(98) 98765-4321")
        self.assertIsNone(f.usuario)  # o atendimento é pelo WhatsApp, sem login
        self.assertEqual(f.whatsapp, "5598987654321")
        self.assertEqual(self.criar_funcionario(matricula="1002", cpf="11144477735", telefone="(98) 3232-1010").whatsapp,
                         "")  # telefone fixo não é atendido


class OrientacaoTestes(TestCase):
    def test_publico_por_contrato_e_versoes(self):
        semed = Contrato.objects.create(sigla="SEMED")
        tjma = Contrato.objects.create(sigla="TJMA")
        categoria = Categoria.objects.create(nome="Vale-Transporte")
        func = Funcionario.objects.create(nome_completo="Ana", matricula="1", cpf="52998224725",
                                          data_nascimento=date(1990, 1, 1), contrato=tjma, cargo="x")
        so_semed = Orientacao.objects.create(titulo="Só SEMED", categoria=categoria, status="publicado",
                                             para_todos=False)
        so_semed.contratos.add(semed)
        todos = Orientacao.objects.create(titulo="Todos", categoria=categoria, status="publicado")
        Orientacao.objects.create(titulo="Rascunho", categoria=categoria)
        self.assertEqual(list(Orientacao.objects.publicadas_para(func)), [todos])

        anexo = Anexo.objects.create(orientacao=todos, titulo="Termo", arquivo="orientacoes/v1.pdf")
        anexo.arquivo = "orientacoes/v2.pdf"
        anexo.save()
        self.assertEqual(anexo.versao, 2)
        self.assertEqual(anexo.versoes_anteriores.get().arquivo.name, "orientacoes/v1.pdf")


class SolicitacaoTestes(TestCase):
    def test_protocolo_gerado(self):
        contrato = Contrato.objects.create(sigla="SEMED")
        func = Funcionario.objects.create(nome_completo="Ana", matricula="1", cpf="52998224725",
                                          data_nascimento=date(1990, 1, 1), contrato=contrato, cargo="x")
        tipo = TipoSolicitacao.objects.create(nome="Cancelamento VT")
        s = Solicitacao.objects.create(funcionario=func, tipo=tipo)
        self.assertRegex(s.protocolo, r"^\d{4}-\d{6}$")


class TrocarSenhaRhTestes(TestCase):
    def setUp(self):
        self.rh = get_user_model().objects.create_superuser("rh_teste", "rh@x.com", "SenhaAntiga!2026")
        self.rh.bloqueado, self.rh.tentativas_falhas = True, 4
        self.rh.save()

    def trocar(self, senha, usuario="rh_teste"):
        with mock.patch.dict(os.environ, {"NOVA_SENHA_RH": senha}):
            call_command("trocar_senha_rh", usuario, stdout=StringIO())

    def test_troca_a_senha_e_destrava(self):
        self.trocar("Cafe-Com-Pao-As-7h")
        self.rh.refresh_from_db()
        self.assertTrue(self.rh.check_password("Cafe-Com-Pao-As-7h"))
        self.assertEqual((self.rh.bloqueado, self.rh.tentativas_falhas), (False, 0))

    def test_recusa_senha_fraca_vazia_ou_provisoria_sem_alterar(self):
        for senha in ["", "123", "123456", "grupo_global"]:
            with self.assertRaises(CommandError):
                self.trocar(senha)
        self.rh.refresh_from_db()
        self.assertTrue(self.rh.check_password("SenhaAntiga!2026"))

    def test_login_inexistente(self):
        with self.assertRaises(CommandError):
            self.trocar("Cafe-Com-Pao-As-7h", usuario="nao_existe")
