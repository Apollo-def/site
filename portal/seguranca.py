"""
Cabeçalhos de segurança em todas as respostas (além dos que o Django já põe).

CSP (Content-Security-Policy): o navegador só carrega scripts, estilos, imagens e fontes do próprio portal.
Se alguém conseguir injetar um <script src="site-malicioso">, o navegador recusa. Também impede o painel de
ser aberto dentro de outro site (moldura usada em golpes) e de enviar formulários para fora.
"""

CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",   # o painel do Django e alguns botões usam pequenos scripts na página
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",                # o QR code do WhatsApp vem como imagem data:
    "font-src 'self'",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])
PERMISSOES = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"


class CabecalhosDeSeguranca:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resposta = self.get_response(request)
        resposta.headers.setdefault("Content-Security-Policy", CSP)
        resposta.headers.setdefault("Permissions-Policy", PERMISSOES)
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        return resposta
