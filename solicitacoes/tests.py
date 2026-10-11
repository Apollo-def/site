from django.core.files.base import ContentFile

from contracheques.tests import Base

from .models import HistoricoSolicitacao, Solicitacao
from .servicos import criar_solicitacao_vt, extensao_pelo_conteudo, tipos_vt_disponiveis, vt_em_andamento

PDF_FALSO = b"%PDF-1.4 termo assinado"


class PedidoVTTestes(Base):
    """O termo de VT chega pelo WhatsApp (ou é lançado pelo RH) e vira um pedido Pendente."""

    def test_termo_vira_pedido_pendente_com_historico(self):
        tipo = tipos_vt_disponiveis(self.clara)["vt_cancelar"]
        solicitacao = criar_solicitacao_vt(self.clara, tipo, ContentFile(PDF_FALSO), ".pdf",
                                           origem=Solicitacao.Origem.WHATSAPP)
        self.assertEqual((solicitacao.funcionario, solicitacao.status), (self.clara, Solicitacao.Status.PENDENTE))
        self.assertEqual(solicitacao.origem, Solicitacao.Origem.WHATSAPP)
        self.assertRegex(solicitacao.protocolo, r"^\d{4}-\d{6}$")
        self.assertTrue(HistoricoSolicitacao.objects.filter(solicitacao=solicitacao).exists())
        self.assertEqual(vt_em_andamento(self.clara), solicitacao)  # até o RH responder, não aceita outro
        self.assertIsNone(vt_em_andamento(self.joao))

    def test_so_aceita_pdf_ou_foto(self):
        self.assertEqual(extensao_pelo_conteudo(PDF_FALSO[:8]), ".pdf")
        self.assertEqual(extensao_pelo_conteudo(b"\xff\xd8\xff\xe0"), ".jpg")
        self.assertIsNone(extensao_pelo_conteudo(b"MZ executavel"))
