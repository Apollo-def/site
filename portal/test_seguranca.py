import json
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from contas.autenticacao import problemas_da_senha
from contas.validadores_senha import SenhaForteValidator
from whatsapp.models import Conversa, Mensagem
from whatsapp.tests import CEL_CLARA, AssistenteBase

TOKEN = "senha-do-gateway-com-mais-de-32-caracteres!"


class SenhaDoRHTestes(TestCase):
    def test_regras_da_senha_forte(self):
        rh = get_user_model()(username="grupo_global")
        self.assertTrue(problemas_da_senha("global@2001", rh))       # nome da empresa
        self.assertTrue(problemas_da_senha("abcdefghij", rh))        # só minúsculas
        self.assertTrue(problemas_da_senha("Ab1!", rh))              # curta
        self.assertEqual(problemas_da_senha("Cafe-Com-Pao-As-7h", rh), [])
        with self.assertRaises(Exception):
            SenhaForteValidator().validate("global@2001", rh)

    def test_login_com_senha_fraca_mostra_aviso_ate_trocar(self):
        get_user_model().objects.create_superuser("grupo_global", "rh@x.com", "global@2001")
        self.client.post(reverse("admin:login"), {"username": "grupo_global", "password": "global@2001"})
        resposta = self.client.get(reverse("admin:index"))
        self.assertContains(resposta, "Sua senha do painel é fraca")
        self.client.post(reverse("admin:password_change"), {
            "old_password": "global@2001", "new_password1": "Cafe-Com-Pao-As-7h", "new_password2": "Cafe-Com-Pao-As-7h"})
        self.assertNotContains(self.client.get(reverse("admin:index")), "Sua senha do painel é fraca")

    def test_troca_para_senha_fraca_e_recusada(self):
        get_user_model().objects.create_superuser("rh", "rh@x.com", "Cafe-Com-Pao-As-7h")
        self.client.post(reverse("admin:login"), {"username": "rh", "password": "Cafe-Com-Pao-As-7h"})
        resposta = self.client.post(reverse("admin:password_change"), {
            "old_password": "Cafe-Com-Pao-As-7h", "new_password1": "global2026", "new_password2": "global2026"})
        self.assertContains(resposta, "Senha fraca")


class CabecalhosTestes(TestCase):
    def test_csp_e_permissoes_em_todas_as_paginas(self):
        resposta = self.client.get("/")
        self.assertIn("frame-ancestors 'none'", resposta["Content-Security-Policy"])
        self.assertIn("default-src 'self'", resposta["Content-Security-Policy"])
        self.assertIn("camera=()", resposta["Permissions-Policy"])
        self.assertEqual(resposta["X-Content-Type-Options"], "nosniff")


class PublicacaoTestes(TestCase):
    @override_settings(DEBUG=False, SECRET_KEY="x" * 50, ADMIN_URL="admin/")
    def test_recusa_painel_em_endereco_obvio(self):
        with self.assertRaisesMessage(CommandError, "endereço óbvio"):
            call_command("verificar_publicacao", stdout=StringIO())

    @override_settings(DEBUG=False, SECRET_KEY="x" * 50, ADMIN_URL="painel-rh-a1b2c3/")
    def test_endereco_secreto_passa(self):
        call_command("verificar_publicacao", stdout=StringIO())


@override_settings(WHATSAPP_CANAL="gateway", WHATSAPP_GATEWAY_TOKEN=TOKEN)
class WhatsappSegurancaTestes(AssistenteBase):
    def test_gateway_recusa_chamada_vinda_da_internet_mesmo_com_a_senha(self):
        corpo = json.dumps({"de": CEL_CLARA, "id": "x1", "texto": "oi"})
        resposta = self.client.post("/whatsapp/gateway/", corpo, content_type="application/json",
                                    headers={"X-Gateway-Token": TOKEN, "X-Forwarded-For": "200.1.2.3"})
        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(Conversa.objects.exists())

    def test_quem_dispara_mensagens_para_de_receber_resposta(self):
        for n in range(15):
            self.falar(f"oi {n}")
        avisos = [r for r in self.respostas() if "muitas mensagens seguidas" in r]
        self.assertEqual(len(avisos), 1)
        self.assertIn("muitas mensagens seguidas", self.ultima())

    def test_limpeza_das_conversas_antigas(self):
        self.comecar()
        Mensagem.objects.update(criada_em=timezone.now() - timedelta(days=400))
        self.falar("obrigada")
        call_command("limpar_whatsapp", stdout=StringIO())
        self.assertEqual(Mensagem.objects.count(), 2)  # só a mensagem de hoje e a resposta dela
