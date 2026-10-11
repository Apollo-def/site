import base64
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from . import duas_etapas
from .views_duas_etapas import CHAVE_SEGREDO_NOVO

SENHA = "Cafe-Com-Pao-As-7h"


class CodigoTestes(SimpleTestCase):
    def test_vetores_oficiais_da_rfc_6238(self):
        # Segredo "12345678901234567890" (SHA-1). A RFC dá 8 dígitos; o app usa os 6 últimos.
        segredo = base64.b32encode(b"12345678901234567890").decode()
        for instante, esperado in [(59, "94287082"), (1111111109, "07081804"), (1234567890, "89005924"),
                                   (2000000000, "69279037")]:
            self.assertEqual(duas_etapas.codigo(segredo, duas_etapas.passo_atual(instante)), esperado[-6:])

    def test_aceita_o_relogio_um_pouco_fora_e_nao_aceita_reuso(self):
        segredo = duas_etapas.novo_segredo()
        agora = 1_800_000_000
        anterior = duas_etapas.codigo(segredo, duas_etapas.passo_atual(agora) - 1)
        passo = duas_etapas.conferir(segredo, anterior, agora=agora)
        self.assertIsNotNone(passo)
        self.assertIsNone(duas_etapas.conferir(segredo, anterior, ultimo_passo_usado=passo, agora=agora))
        velho = duas_etapas.codigo(segredo, duas_etapas.passo_atual(agora) - 5)
        self.assertIsNone(duas_etapas.conferir(segredo, velho, agora=agora))

    def test_codigos_reserva_valem_uma_vez(self):
        codigos, guardados = duas_etapas.novos_codigos_reserva()
        self.assertEqual(len(codigos), 8)
        restantes = duas_etapas.usar_codigo_reserva(guardados, codigos[3])
        self.assertEqual(len(restantes), 7)
        self.assertIsNone(duas_etapas.usar_codigo_reserva(restantes, codigos[3]))

    def test_qr_code_para_o_app(self):
        endereco = duas_etapas.endereco_para_o_app("ABCDEFGH", "grupo_global")
        self.assertTrue(endereco.startswith("otpauth://totp/"))
        self.assertIn("secret=ABCDEFGH", endereco)
        self.assertTrue(duas_etapas.qr_code_svg(endereco).startswith("data:image/svg+xml"))


class FluxoTestes(TestCase):
    def setUp(self):
        cache.clear()
        self.rh = get_user_model().objects.create_superuser("rh", "rh@x.com", SENHA)

    def entrar(self):
        return self.client.post(reverse("admin:login"), {"username": "rh", "password": SENHA})

    def ativar(self):
        self.entrar()
        self.client.get(reverse("admin:duas_etapas_configurar"))
        segredo = self.client.session[CHAVE_SEGREDO_NOVO]
        resposta = self.client.post(reverse("admin:duas_etapas_configurar"), {
            "acao": "ativar", "codigo": duas_etapas.codigo(segredo, duas_etapas.passo_atual())})
        self.rh.refresh_from_db()
        return segredo, resposta

    def test_sem_ativar_aparece_o_convite(self):
        self.entrar()
        self.assertContains(self.client.get(reverse("admin:index")), "ative a verificação em duas etapas")

    def test_ativar_mostra_os_codigos_reserva_e_depois_o_login_pede_o_codigo(self):
        segredo, resposta = self.ativar()
        self.assertTrue(self.rh.duas_etapas_ativa)
        self.assertContains(resposta, "Guarde os seus códigos reserva")
        self.client.logout()
        self.entrar()
        # Com a senha certa, mas sem o código: nenhuma tela do painel abre.
        for url in (reverse("admin:index"), reverse("admin:contas_funcionario_changelist"),
                    reverse("arquivo_privado_admin", kwargs={"caminho": "x.pdf"})):
            self.assertRedirects(self.client.get(url), f"{reverse('admin:duas_etapas_verificar')}?next={url}",
                                 fetch_redirect_response=False)
        proximo = duas_etapas.codigo(segredo, duas_etapas.passo_atual() + 1)  # passo novo (o atual já foi usado)
        self.client.post(reverse("admin:duas_etapas_verificar"), {"codigo": proximo})
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)

    def test_codigo_reserva_entra_e_muitos_erros_travam(self):
        _, resposta = self.ativar()
        reserva = resposta.context["codigos_novos"][0]
        self.client.logout()
        self.entrar()
        self.client.post(reverse("admin:duas_etapas_verificar"), {"codigo": reserva})
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        self.client.logout()
        self.entrar()
        for _ in range(5):
            self.client.post(reverse("admin:duas_etapas_verificar"), {"codigo": "000000"})
        self.rh.refresh_from_db()
        self.assertTrue(self.rh.bloqueado)
        self.assertRedirects(self.client.get(reverse("admin:index")),
                             f"{reverse('admin:login')}?next={reverse('admin:index')}", fetch_redirect_response=False)

    def test_comando_de_emergencia(self):
        self.ativar()
        call_command("desativar_duas_etapas", "rh", stdout=StringIO())
        self.rh.refresh_from_db()
        self.assertFalse(self.rh.duas_etapas_ativa)
        self.assertEqual(self.rh.duas_etapas_segredo, "")
