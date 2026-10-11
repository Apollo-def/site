from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from contracheques.tests import Base, gerar_pdf

from .models import DocumentoFuncionario, LoteDocumento, PendenciaDocumento
from .processamento import ler_pagina


def pagina_termo_vt(nome, cpf):
    return ["TERMO DE OPÇÃO - VALE TRANSPORTE",
            f"Eu, {nome}. portador(a) do CPF: {cpf} e RG: 0000000000000,",
            "residente e domiciliada na(o) RUA TESTE, nº 10 - Bairro: CENTRO, SAO LUIS - MA,",
            "( ) UTILIZAÇÃO de Vale Transporte   ( ) NÃO UTILIZAÇÃO de Vale Transporte"]


def pagina_registro(nome, cpf, matricula="7013"):
    return ["REGISTRO DE EMPREGADO", "Autenticar Matrícula eSocial Nº", f"{matricula} 1234",
            "Empregador CNPJ", "GLOBALTECH BRASIL LTDA 00.000.000/0001-00",
            "Empregado Beneficiários", nome,
            "CTPS Série Data de expedição da CTPS UF CTPS CPF", f"123456 00012 MA {cpf}"]


class LeituraTestes(Base):
    def test_le_cpf_e_nome_do_termo_de_vt(self):
        tem_texto, cpfs, nome = ler_pagina("\n".join(pagina_termo_vt("CLARA MARIA SILVA SANTOS", "529.982.247-25")))
        self.assertEqual((tem_texto, cpfs, nome), (True, ["52998224725"], "CLARA MARIA SILVA SANTOS"))

    def test_le_cpf_do_registro_e_ignora_cnpj(self):
        _, cpfs, nome = ler_pagina("\n".join(pagina_registro("JOAO PEREIRA", "111.444.777-35")))
        self.assertEqual(cpfs, ["11144477735"])
        self.assertEqual(nome, "JOAO PEREIRA")


class EnvioEmLoteTestes(Base):
    def enviar_documento(self, paginas, tipo="termo_vt"):
        self.client.force_login(self.rh)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("admin:documentos_lote_enviar"), {
                "arquivo": SimpleUploadedFile("todos.pdf", gerar_pdf(paginas), content_type="application/pdf"),
                "tipo": tipo, "titulo": ""})
        return LoteDocumento.objects.latest("pk")

    def test_separa_pelo_cpf_e_lista_pendencias(self):
        lote = self.enviar_documento([
            pagina_termo_vt("CLARA MARIA SILVA SANTOS", "529.982.247-25"),
            pagina_termo_vt("JOAO PEREIRA", "111.444.777-35"),
            pagina_termo_vt("FULANO NAO CADASTRADO", "123.456.789-09"),
            ["PAGINA QUALQUER SEM NENHUM CPF, SÓ TEXTO DE TESTE AQUI"],
        ])
        self.assertEqual(lote.status, LoteDocumento.Status.CONCLUIDO)
        self.assertEqual(lote.titulo, "Termo de opção de vale-transporte")
        self.assertEqual((lote.total_distribuidos, lote.total_nao_encontrados, lote.total_sem_cpf), (2, 1, 1))
        self.assertEqual(self.clara.documentos.get().tipo, "termo_vt")
        self.assertEqual(set(PendenciaDocumento.objects.values_list("motivo", flat=True)), {"nao_cadastrado", "sem_cpf"})
        resposta = self.client.get(reverse("admin:documentos_lote_relatorio", args=[lote.pk]))
        self.assertContains(resposta, "FULANO NAO CADASTRADO")

    def test_novo_envio_substitui_o_anterior(self):
        self.enviar_documento([pagina_termo_vt("CLARA MARIA SILVA SANTOS", "529.982.247-25")])
        self.enviar_documento([pagina_termo_vt("CLARA MARIA SILVA SANTOS", "529.982.247-25")])
        self.assertEqual(self.clara.documentos.filter(tipo="termo_vt").count(), 1)
        self.assertEqual(self.clara.documentos.get().lote, LoteDocumento.objects.latest("pk"))

    def test_nome_diferente_vira_pendencia_e_rh_vincula(self):
        lote = self.enviar_documento([pagina_registro("PEDRO ALVES COSTA", "529.982.247-25")], tipo="registro_empregado")
        pendencia = PendenciaDocumento.objects.get(motivo="nome_divergente")
        self.assertFalse(DocumentoFuncionario.objects.exists())
        self.client.post(reverse("admin:documentos_pendente_vincular", args=[pendencia.pk]),
                         {"identificador": self.clara.cpf})
        self.assertEqual(self.clara.documentos.get().tipo, "registro_empregado")
        lote.refresh_from_db()
        self.assertEqual(lote.total_distribuidos, 1)

    def test_apagar_documento_apaga_o_arquivo_do_disco(self):
        self.enviar_documento([pagina_termo_vt("CLARA MARIA SILVA SANTOS", "529.982.247-25")])
        documento = self.clara.documentos.get()
        nome, armazenamento = documento.arquivo.name, documento.arquivo.storage
        self.assertTrue(armazenamento.exists(nome))
        with self.captureOnCommitCallbacks(execute=True):
            documento.delete()
        self.assertFalse(armazenamento.exists(nome))
