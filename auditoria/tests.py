from django.test import RequestFactory, SimpleTestCase, override_settings

from .servicos import obter_ip


class ObterIpTestes(SimpleTestCase):
    def pedido(self, **meta):
        return RequestFactory().get("/", REMOTE_ADDR="127.0.0.1", **meta)

    @override_settings(CONFIAR_CABECALHO_PROXY=True)
    def test_usa_o_ip_que_o_proxy_acrescentou_e_nao_o_inventado_pelo_visitante(self):
        # O visitante manda "1.1.1.1"; o proxy acrescenta o IP real no fim.
        self.assertEqual(obter_ip(self.pedido(HTTP_X_FORWARDED_FOR="1.1.1.1, 200.10.20.30")), "200.10.20.30")

    @override_settings(CONFIAR_CABECALHO_PROXY=False)
    def test_sem_proxy_ignora_o_cabecalho(self):
        self.assertEqual(obter_ip(self.pedido(HTTP_X_FORWARDED_FOR="1.1.1.1")), "127.0.0.1")

    @override_settings(CONFIAR_CABECALHO_PROXY=True)
    def test_ip_invalido_vira_nada(self):
        self.assertIsNone(obter_ip(RequestFactory().get("/", REMOTE_ADDR="", HTTP_X_FORWARDED_FOR="lixo")))
