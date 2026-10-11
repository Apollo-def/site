from datetime import date
from io import StringIO

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from contas.models import Contrato, Funcionario


class PainelAdminTestes(TestCase):
    """Abre a lista e o formulário de cada cadastro do painel para garantir que nada quebra."""

    def setUp(self):
        call_command("carregar_dados_iniciais", stdout=StringIO())
        Funcionario.objects.create(nome_completo="Ana", matricula="1", cpf="52998224725",
                                   data_nascimento=date(1990, 1, 1), contrato=Contrato.objects.first(), cargo="x")
        self.admin = get_user_model().objects.create_superuser("rh", "rh@exemplo.com", "SenhaForte!2026")
        self.client.force_login(self.admin)

    def test_todas_as_telas_abrem(self):
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 200)
        for model, model_admin in admin.site._registry.items():
            meta = model._meta
            lista = reverse(f"admin:{meta.app_label}_{meta.model_name}_changelist")
            self.assertEqual(self.client.get(lista, follow=True).status_code, 200, lista)
            if model_admin.has_add_permission(self.client.get(lista).wsgi_request):
                novo = reverse(f"admin:{meta.app_label}_{meta.model_name}_add")
                self.assertEqual(self.client.get(novo, follow=True).status_code, 200, novo)

    def test_arquivo_privado_exige_login_de_rh(self):
        self.client.logout()
        resposta = self.client.get(reverse("arquivo_privado_admin", kwargs={"caminho": "qualquer.pdf"}))
        self.assertEqual(resposta.status_code, 302)  # manda para o login

    def test_menu_lateral_marca_a_tela_atual_e_mostra_pendencias(self):
        resposta = self.client.get(reverse("admin:contas_funcionario_changelist"))
        self.assertContains(resposta, 'class="pn-menu"')
        self.assertContains(resposta, f'class="pn-item pn-ativo" href="{reverse("admin:contas_funcionario_changelist")}"')
        self.assertContains(resposta, "Conexão (QR code)")

    def test_menu_so_mostra_o_que_a_pessoa_pode_abrir(self):
        from django.contrib.auth.models import Permission
        atendente = get_user_model().objects.create_user("atendente", password="SenhaForte!2026", is_staff=True)
        atendente.user_permissions.add(Permission.objects.get(codename="view_conversa"))
        self.client.force_login(atendente)
        resposta = self.client.get(reverse("admin:whatsapp_conversa_changelist"))
        self.assertContains(resposta, "Atendimentos")
        for escondido in ("Funcionários", "Auditoria", "Conexão (QR code)", "Enviar contracheques"):
            self.assertNotContains(resposta, escondido)


class PaginaPublicaTestes(TestCase):
    def test_inicio_indica_o_whatsapp(self):
        from contas.models import ConfiguracaoPortal
        resposta = self.client.get("/")
        self.assertContains(resposta, "O atendimento agora é pelo WhatsApp")
        self.assertNotContains(resposta, "wa.me")  # sem número divulgado, não mostra o botão
        config = ConfiguracaoPortal.atual()
        config.whatsapp_numero_publico = "(98) 98888-7777"
        config.save()
        self.assertContains(self.client.get("/"), "https://wa.me/5598988887777")

    def test_telas_antigas_do_funcionario_nao_existem_mais(self):
        for endereco in ("/inicio/", "/contracheques/", "/meus-dados/", "/solicitacoes/", "/primeiro-acesso/",
                         "/sw.js", "/manifest.webmanifest"):
            self.assertEqual(self.client.get(endereco).status_code, 404, endereco)

    def test_login_do_painel_abre(self):
        resposta = self.client.get(reverse("admin:login"))
        self.assertContains(resposta, "Painel do RH")
        self.assertNotContains(resposta, 'class="pn-menu"')  # sem login, sem menu


class SessaoNoWindowsTestes(TestCase):
    """O Windows trava por um instante o arquivo da sessão que outra requisição está regravando."""

    def test_arquivo_travado_espera_e_grava(self):
        import os
        from unittest import mock

        from portal.sessoes import SessionStore

        sessao = SessionStore()
        sessao["teste"] = 1
        sessao.create()
        abrir, falhas = os.open, []

        def travado_uma_vez(*args, **kwargs):
            if not falhas:
                falhas.append(1)
                raise PermissionError(13, "Permission denied")
            return abrir(*args, **kwargs)

        sessao["teste"] = 2
        with mock.patch("django.contrib.sessions.backends.file.os.open", side_effect=travado_uma_vez):
            sessao.save()
        self.assertEqual(falhas, [1])
        self.assertEqual(SessionStore(sessao.session_key).load()["teste"], 2)
        sessao.delete()


class BancoOcupadoTestes(TestCase):
    """Dois PDFs grandes ao mesmo tempo: o SQLite dava "database is locked" e o envio ficava com erro."""

    def test_banco_ocupado_espera_e_tenta_de_novo(self):
        from unittest import mock

        from django.db import OperationalError

        from contracheques.models import LoteContracheque
        from portal import lotes

        lote = LoteContracheque.objects.create(ano=2026, mes=6, arquivo="x.pdf")
        chamadas = []

        def etapas(lote):
            chamadas.append(1)
            if len(chamadas) == 1:
                raise OperationalError("database is locked")
            LoteContracheque.objects.filter(pk=lote.pk).update(status=LoteContracheque.Status.CONCLUIDO)

        with mock.patch.object(lotes.time, "sleep") as esperou:
            lotes.processar_com_status(LoteContracheque, lote.pk, etapas)
        lote.refresh_from_db()
        self.assertEqual((len(chamadas), esperou.call_count, lote.status), (2, 1, LoteContracheque.Status.CONCLUIDO))

    def test_outro_erro_marca_o_lote_com_erro_na_hora(self):
        from contracheques.models import LoteContracheque
        from portal import lotes

        lote = LoteContracheque.objects.create(ano=2026, mes=6, arquivo="x.pdf")

        def etapas(lote):
            raise ValueError("o PDF está protegido por senha")

        lotes.processar_com_status(LoteContracheque, lote.pk, etapas)
        lote.refresh_from_db()
        self.assertEqual(lote.status, LoteContracheque.Status.ERRO)
        self.assertIn("protegido por senha", lote.mensagem_erro)


class CookiesDoPortalTestes(TestCase):
    """Outro sistema Django aberto em 127.0.0.1 sobrescrevia o "csrftoken" e o formulário do painel dava 403."""

    def test_nomes_proprios_e_pagina_explicando_o_que_fazer(self):
        from django.test import Client

        admin_ = get_user_model().objects.create_superuser("rh", "rh@x.com", "SenhaForte!2026")
        cliente = Client(enforce_csrf_checks=True)
        cliente.force_login(admin_)
        self.assertIn("portal_rh_sessao", cliente.cookies)
        cliente.get(reverse("admin:index"))
        self.assertIn("portal_rh_csrf", cliente.cookies)
        cliente.cookies["csrftoken"] = "x" * 32  # o cookie do outro sistema não atrapalha mais
        resposta = cliente.post(reverse("admin:password_change"), {"csrfmiddlewaretoken": "token-velho"},
                                HTTP_REFERER="https://site-estranho.com/")
        self.assertEqual(resposta.status_code, 403)
        self.assertContains(resposta, "Esta página ficou desatualizada", status_code=403)
        self.assertNotContains(resposta, "site-estranho", status_code=403)
