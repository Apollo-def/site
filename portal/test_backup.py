import io
import json
import shutil
import tempfile
import zipfile
from datetime import date
from pathlib import Path

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from contas.models import Contrato, Funcionario

PASTA = tempfile.mkdtemp(prefix="portal_backup_testes_")
SENHA = "senha-de-restauracao-bem-grande-123"


def montar_zip(dados, arquivos=None):
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w") as zip_:
        zip_.writestr("dados.json", json.dumps(dados))
        for nome, conteudo in (arquivos or {}).items():
            zip_.writestr(nome, conteudo)
    return saida.getvalue()


BACKUP_VALIDO = [
    {"model": "contas.contrato", "pk": 50, "fields": {"sigla": "SEMCAS", "nome": "Assistência", "ativo": True}},
    {"model": "contas.funcionario", "pk": 70, "fields": {
        "nome_completo": "ANA TESTE", "matricula": "900", "cpf": "52998224725", "data_nascimento": "1990-03-07",
        "contrato": 50, "situacao": "ativo", "criado_em": "2026-01-01T00:00:00Z", "atualizado_em": "2026-01-01T00:00:00Z"}},
]


@override_settings(PRIVATE_MEDIA_ROOT=PASTA, RESTAURAR_BACKUP_SENHA=SENHA)
class RestaurarBackupTestes(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(PASTA, ignore_errors=True)

    def setUp(self):
        cache.clear()

    def enviar(self, conteudo, senha=SENHA):
        return self.client.post(reverse("restaurar_backup"), {
            "senha": senha, "arquivo": SimpleUploadedFile("backup.zip", conteudo, content_type="application/zip")})

    def test_restaura_dados_e_arquivos_e_depois_se_desliga(self):
        resposta = self.enviar(montar_zip(BACKUP_VALIDO, {"arquivos/documentos/2026/09/abc.pdf": b"%PDF-teste"}))
        self.assertContains(resposta, "Os dados foram restaurados")
        self.assertEqual(Funcionario.objects.get().contrato.sigla, "SEMCAS")
        self.assertEqual((Path(PASTA) / "documentos/2026/09/abc.pdf").read_bytes(), b"%PDF-teste")
        self.assertEqual(self.client.get(reverse("restaurar_backup")).status_code, 404)

    @override_settings(RESTAURAR_BACKUP_SENHA="")
    def test_sem_senha_configurada_a_tela_nao_existe(self):
        self.assertEqual(self.client.get(reverse("restaurar_backup")).status_code, 404)

    def test_com_funcionarios_cadastrados_a_tela_nao_existe(self):
        contrato = Contrato.objects.create(sigla="SEMED")
        Funcionario.objects.create(nome_completo="X", matricula="1", cpf="11144477735",
                                   data_nascimento=date(1990, 1, 1), contrato=contrato)
        self.assertEqual(self.client.get(reverse("restaurar_backup")).status_code, 404)

    def test_senha_errada_nao_restaura_e_trava_depois_de_5(self):
        for _ in range(5):
            self.assertContains(self.enviar(montar_zip(BACKUP_VALIDO), senha="errada"), "Senha incorreta")
        self.assertContains(self.enviar(montar_zip(BACKUP_VALIDO)), "Muitas tentativas erradas")
        self.assertFalse(Funcionario.objects.exists())

    def test_recusa_caminho_que_sai_da_pasta(self):
        resposta = self.enviar(montar_zip(BACKUP_VALIDO, {"arquivos/../../fora.txt": b"x"}))
        self.assertContains(resposta, "Caminho inválido")
        self.assertFalse(Funcionario.objects.exists())

    def test_recusa_arquivo_que_nao_e_zip(self):
        self.assertContains(self.enviar(b"nao sou zip"), "não é um backup .zip válido")
