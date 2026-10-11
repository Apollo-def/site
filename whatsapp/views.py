import base64
import binascii
import hashlib
import hmac
import json
import logging
import threading

from django.conf import settings
from django.core.signing import BadSignature, TimestampSigner
from django.db import connection
from django.http import FileResponse, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from auditoria.models import LogAuditoria
from auditoria.servicos import registrar
from contas.models import ConfiguracaoPortal, Funcionario
from contracheques.models import Contracheque
from documentos.models import DocumentoFuncionario

from .atendimento import HORAS_LINK, SALT_LINK, receber
from .canal import CanalMeta, ErroCanal

logger = logging.getLogger(__name__)
TIPOS_COM_ARQUIVO = {"image": "foto.jpg", "document": "documento.pdf"}


# ---------------------------------------------------------------------------
# Webhook da Meta (WhatsApp Business Cloud API)
# ---------------------------------------------------------------------------
def assinatura_valida(corpo, assinatura):
    """A Meta assina cada aviso com o App Secret: sem isso, qualquer um poderia se passar por ela."""
    if not settings.WHATSAPP_APP_SECRET:
        return False
    esperado = "sha256=" + hmac.new(settings.WHATSAPP_APP_SECRET.encode(), corpo, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, assinatura or "")


def processar_mensagem_meta(mensagem):
    """Uma mensagem recebida (formato da Meta) vira uma entrada no atendimento."""
    tipo = mensagem.get("type")
    texto, midia = "", None
    if tipo == "text":
        texto = mensagem.get("text", {}).get("body", "")
    elif tipo == "button":
        texto = mensagem.get("button", {}).get("text", "")
    elif tipo == "audio":  # vai para o RH ouvir no painel
        try:
            conteudo = CanalMeta().baixar_midia(mensagem.get("audio", {})["id"])
        except (ErroCanal, KeyError):
            logger.exception("Não consegui baixar o áudio %s", mensagem.get("id"))
            conteudo = b""
        receber(mensagem.get("from", ""), midia={"conteudo": conteudo, "nome": "audio.ogg"},
                id_externo=mensagem.get("id", ""), tipo="audio")
        return
    elif tipo in ("video", "sticker"):
        receber(mensagem.get("from", ""), id_externo=mensagem.get("id", ""),
                tipo="figurinha" if tipo == "sticker" else tipo)
        return
    elif tipo in TIPOS_COM_ARQUIVO:
        dados = mensagem.get(tipo, {})
        texto = dados.get("caption", "")
        try:
            conteudo = CanalMeta().baixar_midia(dados["id"])
        except (ErroCanal, KeyError):
            logger.exception("Não consegui baixar a mídia %s", dados.get("id"))
            conteudo = b""
        midia = {"conteudo": conteudo, "nome": dados.get("filename") or TIPOS_COM_ARQUIVO[tipo]}
    receber(mensagem.get("from", ""), texto=texto, midia=midia, id_externo=mensagem.get("id", ""))


def _processar(mensagens, tratar=None):
    tratar = tratar or processar_mensagem_meta

    def rodar():
        try:
            for mensagem in mensagens:
                try:
                    tratar(mensagem)
                except Exception:  # uma mensagem com problema não pode travar as outras
                    logger.exception("Erro ao atender a mensagem %s", mensagem.get("id"))
        finally:
            if getattr(settings, "PROCESSAR_EM_SEGUNDO_PLANO", True):
                connection.close()

    if getattr(settings, "PROCESSAR_EM_SEGUNDO_PLANO", True):
        threading.Thread(target=rodar, daemon=True, name="whatsapp").start()  # responde logo à Meta
    else:
        rodar()


@csrf_exempt  # a Meta não tem como mandar o token CSRF; a proteção é a assinatura conferida abaixo
def webhook(request):
    if request.method == "GET":  # a Meta confere o endereço ao cadastrar o webhook
        token = request.GET.get("hub.verify_token", "")
        if (settings.WHATSAPP_VERIFY_TOKEN and request.GET.get("hub.mode") == "subscribe"
                and hmac.compare_digest(token, settings.WHATSAPP_VERIFY_TOKEN)):
            return HttpResponse(request.GET.get("hub.challenge", ""), content_type="text/plain")
        return HttpResponseForbidden()
    if request.method != "POST":
        return HttpResponse(status=405)
    if not assinatura_valida(request.body, request.headers.get("X-Hub-Signature-256")):
        return HttpResponseForbidden()
    try:
        dados = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)
    mensagens = [m for entrada in dados.get("entry", []) for mudanca in entrada.get("changes", [])
                 for m in mudanca.get("value", {}).get("messages", [])]
    if mensagens:
        _processar(mensagens)
    return HttpResponse("ok")


# ---------------------------------------------------------------------------
# Gateway por QR code (gateway-whatsapp/, no mesmo PC): repassa cada mensagem recebida para cá
# ---------------------------------------------------------------------------
CABECALHOS_DE_PROXY = ("X-Forwarded-For", "X-Real-Ip", "Cf-Connecting-Ip", "Forwarded")


def chamada_local(request):
    """
    Veio do próprio PC (o gateway), e não da internet? Tudo o que passa pelo túnel (ngrok/Cloudflare) chega com
    o endereço do visitante nos cabeçalhos de proxy, então é recusado mesmo que alguém descubra a senha.
    """
    if request.META.get("REMOTE_ADDR") not in ("127.0.0.1", "::1"):
        return False
    return not any(request.headers.get(nome) for nome in CABECALHOS_DE_PROXY)


def token_do_gateway_valido(request):
    token = settings.WHATSAPP_GATEWAY_TOKEN
    return len(token) >= 20 and hmac.compare_digest(request.headers.get("X-Gateway-Token", ""), token)


def processar_mensagem_gateway(dados):
    midia = None
    if dados.get("midia"):
        try:
            conteudo = base64.b64decode(dados["midia"].get("base64") or "", validate=True)
        except (binascii.Error, ValueError):
            conteudo = b""
        midia = {"conteudo": conteudo, "nome": str(dados["midia"].get("nome") or "documento.pdf")}
    receber(dados["de"], texto=str(dados.get("texto") or ""), midia=midia, id_externo=str(dados.get("id") or ""),
            tipo=str(dados.get("tipo") or ""))


@csrf_exempt  # chamado pelo gateway, não por um navegador; a proteção é a senha conferida abaixo
def gateway(request):
    if request.method != "POST":
        return HttpResponse(status=405)
    if settings.WHATSAPP_CANAL != "gateway" or not chamada_local(request) or not token_do_gateway_valido(request):
        return HttpResponseForbidden()
    try:
        dados = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)
    if dados.get("tipo") == "reacao_grupo":  # 👍 do ADM numa ocorrência
        from ocorrencias.leitura import receber_reacao_do_grupo
        _processar([dados], receber_reacao_do_grupo)
        return HttpResponse("ok")
    if dados.get("tipo") == "ocorrencia_grupo":  # grupo dos supervisores: ninguém recebe resposta escrita
        from ocorrencias.leitura import receber_mensagem_do_grupo
        try:  # na hora (e não em segundo plano): o gateway reage com 🤖 só se registrou alguma ocorrência
            criadas = receber_mensagem_do_grupo(dados)
        except Exception:
            logger.exception("Erro ao ler a mensagem %s do grupo de ocorrências", dados.get("id"))
            criadas = []
        return JsonResponse({"reagir": "🤖"} if criadas else {}, json_dumps_params={"ensure_ascii": False})
    numero = "".join(c for c in str(dados.get("de", "")) if c.isdigit())
    if not 10 <= len(numero) <= 15:
        return HttpResponse(status=400)
    dados["de"] = numero
    _processar([dados], processar_mensagem_gateway)
    return HttpResponse("ok")


# ---------------------------------------------------------------------------
# Link seguro de documento (vale 24 horas)
# ---------------------------------------------------------------------------
@never_cache
def documento(request, token):
    try:
        dados = TimestampSigner(salt=SALT_LINK).unsign_object(token, max_age=HORAS_LINK * 3600)
    except BadSignature:  # inclui o link vencido (SignatureExpired)
        dados = {}
    funcionario = Funcionario.objects.filter(pk=dados.get("f")).first() if dados else None
    arquivo = objeto = None
    if funcionario and dados.get("t") == "termo_padrao":
        objeto = ConfiguracaoPortal.atual()
        arquivo = objeto.termo_vt_padrao or None
        nome = "termo_vale_transporte.pdf"
    elif funcionario:
        modelo = Contracheque if dados.get("t") == "contracheque" else DocumentoFuncionario
        objeto = modelo.objects.filter(pk=dados.get("id"), funcionario=funcionario).first()
        arquivo = objeto.arquivo if objeto else None
        nome = "contracheque.pdf" if modelo is Contracheque else "documento.pdf"
    if arquivo is None:
        return render(request, "whatsapp/link_expirado.html", status=404)
    registrar(LogAuditoria.Acao.BAIXOU_DOCUMENTO, request=request, usuario=funcionario.usuario, objeto=objeto,
              identificador=funcionario.matricula, descricao="Aberto pelo link enviado no WhatsApp")
    resposta = FileResponse(arquivo.open("rb"), filename=nome, content_type="application/pdf")
    resposta["Cache-Control"] = "private, no-store"
    resposta["Referrer-Policy"] = "no-referrer"
    return resposta
