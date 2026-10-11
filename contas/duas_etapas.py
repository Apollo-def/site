"""
Verificação em duas etapas do painel do RH (código de 6 números do Google Authenticator, Microsoft
Authenticator, Authy...). Padrão TOTP (RFC 6238): o celular e o sistema calculam o mesmo código a partir de
um segredo combinado uma vez pelo QR code e do relógio (muda a cada 30 s).

- Cada código só vale uma vez (não dá para reaproveitar um código visto por cima do ombro).
- Códigos reserva (8, de uso único, guardados criptografados) para quem perder o celular.
- Emergência no próprio PC servidor: python manage.py desativar_duas_etapas USUARIO
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

import segno
from django.contrib.auth.hashers import check_password, make_password

PASSO_SEGUNDOS = 30
DIGITOS = 6
TOLERANCIA = 1        # aceita o código anterior e o próximo (relógio do celular um pouco adiantado/atrasado)
EMISSOR = "Painel do RH - Grupo Global"
QUANTIDADE_RESERVA = 8


def novo_segredo():
    return base64.b32encode(secrets.token_bytes(20)).decode()


def codigo(segredo, passo):
    chave = base64.b32decode(segredo + "=" * (-len(segredo) % 8))
    resumo = hmac.new(chave, struct.pack(">Q", passo), hashlib.sha1).digest()
    deslocamento = resumo[-1] & 0x0F
    numero = struct.unpack(">I", resumo[deslocamento:deslocamento + 4])[0] & 0x7FFFFFFF
    return str(numero % 10 ** DIGITOS).zfill(DIGITOS)


def passo_atual(agora=None):
    return int((agora if agora is not None else time.time()) // PASSO_SEGUNDOS)


def conferir(segredo, digitado, ultimo_passo_usado=0, agora=None):
    """Devolve o passo do código aceito (para impedir reuso) ou None."""
    digitado = "".join(c for c in str(digitado or "") if c.isdigit())
    if len(digitado) != DIGITOS or not segredo:
        return None
    atual = passo_atual(agora)
    for passo in range(atual - TOLERANCIA, atual + TOLERANCIA + 1):
        if passo > ultimo_passo_usado and hmac.compare_digest(codigo(segredo, passo), digitado):
            return passo
    return None


def endereco_para_o_app(segredo, usuario):
    rotulo = quote(f"{EMISSOR}:{usuario}")
    return f"otpauth://totp/{rotulo}?secret={segredo}&issuer={quote(EMISSOR)}&digits={DIGITOS}&period={PASSO_SEGUNDOS}"


def qr_code_svg(texto):
    """QR code como data:image/svg+xml (a CSP do portal aceita imagens data:)."""
    return segno.make(texto, error="m").svg_data_uri(scale=5, border=2)


def segredo_formatado(segredo):
    return " ".join(segredo[i:i + 4] for i in range(0, len(segredo), 4))


def novos_codigos_reserva():
    """(códigos para mostrar UMA vez, versões criptografadas para guardar)."""
    codigos = [f"{secrets.randbelow(10 ** 4):04d}-{secrets.randbelow(10 ** 4):04d}" for _ in range(QUANTIDADE_RESERVA)]
    return codigos, [make_password(c) for c in codigos]


def usar_codigo_reserva(guardados, digitado):
    """Se bater com um código reserva, devolve a lista sem ele (uso único); senão None."""
    digitado = "".join(c for c in str(digitado or "") if c.isdigit())
    if len(digitado) != 8:
        return None
    formatado = f"{digitado[:4]}-{digitado[4:]}"
    for i, guardado in enumerate(guardados):
        if check_password(formatado, guardado):
            return guardados[:i] + guardados[i + 1:]
    return None
