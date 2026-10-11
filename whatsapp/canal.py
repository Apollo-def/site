"""
Por onde as mensagens saem: o simulador do painel (testes), o gateway por QR code (gateway-whatsapp/)
ou a API oficial da Meta (WhatsApp Business Cloud API). O atendimento só conversa com esta "cara" comum, então o que
funciona no simulador funciona igual no WhatsApp de verdade.

Configuração da Meta no .env: WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_APP_SECRET,
WHATSAPP_VERIFY_TOKEN (e, para avisos fora da janela de 24 h, WHATSAPP_MODELO_AVISO).
"""
import base64
import json
import logging
import mimetypes
import urllib.error
import urllib.request
import uuid

from django.conf import settings

logger = logging.getLogger(__name__)


class ErroCanal(Exception):
    pass


class Canal:
    """Interface comum. Cada envio devolve o id da mensagem no WhatsApp (ou '' no simulador)."""

    def enviar_texto(self, conversa, texto):
        raise NotImplementedError

    def enviar_documento(self, conversa, conteudo, nome_arquivo, legenda=""):
        raise NotImplementedError

    def enviar_aviso(self, conversa, texto):
        """Mensagem que o sistema inicia (ex.: resposta do RH dias depois). Fora da janela de 24 h da Meta,
        só vai por um modelo aprovado."""
        return self.enviar_texto(conversa, texto)


class CanalSimulador(Canal):
    """Não manda nada para fora: as respostas ficam gravadas na conversa e aparecem no simulador do painel."""

    def enviar_texto(self, conversa, texto):
        return ""

    def enviar_documento(self, conversa, conteudo, nome_arquivo, legenda=""):
        return ""


def meta_configurada():
    return bool(settings.WHATSAPP_TOKEN and settings.WHATSAPP_PHONE_NUMBER_ID)


class CanalMeta(Canal):
    """WhatsApp Business Cloud API (Graph API da Meta)."""

    TIMEOUT = 20

    def _url(self, caminho):
        return f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSAO}/{caminho}"

    def _pedido(self, url, dados=None, corpo=None, tipo="application/json", metodo=None):
        cabecalhos = {"Authorization": f"Bearer {settings.WHATSAPP_TOKEN}"}
        if dados is not None:
            corpo = json.dumps(dados).encode()
        if corpo is not None:
            cabecalhos["Content-Type"] = tipo
        pedido = urllib.request.Request(url, data=corpo, headers=cabecalhos, method=metodo)
        try:
            with urllib.request.urlopen(pedido, timeout=self.TIMEOUT) as resposta:
                return resposta.read()
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode("utf-8", "replace")[:500]
            raise ErroCanal(f"Meta respondeu {erro.code}: {detalhe}") from erro
        except urllib.error.URLError as erro:
            raise ErroCanal(f"Sem conexão com a Meta: {erro.reason}") from erro

    def _mensagem(self, conversa, conteudo):
        dados = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": conversa.numero, **conteudo}
        resposta = json.loads(self._pedido(self._url(f"{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"), dados))
        return (resposta.get("messages") or [{}])[0].get("id", "")

    def enviar_texto(self, conversa, texto):
        return self._mensagem(conversa, {"type": "text", "text": {"body": texto[:4096], "preview_url": True}})

    def _subir_arquivo(self, conteudo, nome_arquivo):
        tipo = mimetypes.guess_type(nome_arquivo)[0] or "application/octet-stream"
        fronteira = uuid.uuid4().hex
        partes = []
        for campo, valor in (("messaging_product", "whatsapp"), ("type", tipo)):
            partes.append(f'--{fronteira}\r\nContent-Disposition: form-data; name="{campo}"\r\n\r\n{valor}\r\n'.encode())
        partes.append(f'--{fronteira}\r\nContent-Disposition: form-data; name="file"; filename="{nome_arquivo}"\r\n'
                      f"Content-Type: {tipo}\r\n\r\n".encode() + conteudo + b"\r\n")
        partes.append(f"--{fronteira}--\r\n".encode())
        resposta = self._pedido(self._url(f"{settings.WHATSAPP_PHONE_NUMBER_ID}/media"), corpo=b"".join(partes),
                                tipo=f"multipart/form-data; boundary={fronteira}")
        return json.loads(resposta)["id"]

    def enviar_documento(self, conversa, conteudo, nome_arquivo, legenda=""):
        id_arquivo = self._subir_arquivo(conteudo, nome_arquivo)
        documento = {"id": id_arquivo, "filename": nome_arquivo}
        if legenda:
            documento["caption"] = legenda[:1024]
        return self._mensagem(conversa, {"type": "document", "document": documento})

    def enviar_aviso(self, conversa, texto):
        if conversa.dentro_da_janela or not settings.WHATSAPP_MODELO_AVISO:
            return self.enviar_texto(conversa, texto)
        # Fora das 24 h: modelo aprovado na Meta com uma variável ({{1}}) que recebe o texto do aviso.
        return self._mensagem(conversa, {"type": "template", "template": {
            "name": settings.WHATSAPP_MODELO_AVISO,
            "language": {"code": settings.WHATSAPP_MODELO_IDIOMA},
            "components": [{"type": "body", "parameters": [{"type": "text", "text": texto[:1000]}]}],
        }})

    def baixar_midia(self, id_midia):
        """Foto/PDF que o funcionário mandou: primeiro pega o endereço, depois o arquivo."""
        info = json.loads(self._pedido(self._url(id_midia)))
        return self._pedido(info["url"])


class CanalGateway(Canal):
    """WhatsApp conectado por QR code (gateway-whatsapp/, rodando no mesmo PC). Sem a regra das 24 h da Meta."""

    # O gateway manda uma mensagem por vez, em ritmo de gente (pausa + limite por minuto). Em dia de muito
    # pedido a mensagem espera na fila; o atendimento roda em segundo plano, então pode esperar.
    TIMEOUT = 300
    TIMEOUT_CONSULTA = 10

    def _pedido(self, caminho, dados=None, metodo=None):
        timeout = self.TIMEOUT if caminho == "/enviar" else self.TIMEOUT_CONSULTA
        corpo = json.dumps(dados).encode() if dados is not None else None
        cabecalhos = {"X-Gateway-Token": settings.WHATSAPP_GATEWAY_TOKEN, "Content-Type": "application/json"}
        pedido = urllib.request.Request(settings.WHATSAPP_GATEWAY_URL.rstrip("/") + caminho, data=corpo,
                                        headers=cabecalhos, method=metodo)
        try:
            with urllib.request.urlopen(pedido, timeout=timeout) as resposta:
                return json.loads(resposta.read() or b"{}")
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode("utf-8", "replace")[:300]
            raise ErroCanal(f"Gateway respondeu {erro.code}: {detalhe}") from erro
        except (urllib.error.URLError, OSError) as erro:
            raise ErroCanal(f"Gateway do WhatsApp desligado ou inacessível ({erro})") from erro

    def enviar_texto(self, conversa, texto):
        return self._pedido("/enviar", {"para": conversa.numero, "tipo": "texto", "texto": texto}).get("id", "")

    def enviar_documento(self, conversa, conteudo, nome_arquivo, legenda=""):
        dados = {"para": conversa.numero, "tipo": "documento", "base64": base64.b64encode(conteudo).decode(),
                 "nome": nome_arquivo, "legenda": legenda, "mime": mimetypes.guess_type(nome_arquivo)[0] or ""}
        return self._pedido("/enviar", dados).get("id", "")

    def situacao(self):
        """{'estado': 'conectado'|'aguardando_qr'|..., 'qr': data-URL da imagem, 'numero': ...}"""
        return self._pedido("/status")

    def desconectar(self):
        return self._pedido("/desconectar", {})


def gateway_configurado():
    return settings.WHATSAPP_CANAL == "gateway" and len(settings.WHATSAPP_GATEWAY_TOKEN) >= 20


def canal_para(conversa):
    if conversa.simulada:
        return CanalSimulador()
    return CanalGateway() if settings.WHATSAPP_CANAL == "gateway" else CanalMeta()
