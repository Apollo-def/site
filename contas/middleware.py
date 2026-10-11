"""
Segunda etapa do login do RH: quem tem a verificação em duas etapas ativa só passa do login depois de digitar
o código do celular. Enquanto isso, qualquer página que exige login leva para a tela do código.
"""
from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

CHAVE_VERIFICADO = "duas_etapas_ok"  # na sessão: id do usuário que já digitou o código neste login


def verificado(request):
    return request.session.get(CHAVE_VERIFICADO) == request.user.pk


class DuasEtapasMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        usuario = getattr(request, "user", None)
        if (usuario is not None and usuario.is_authenticated and usuario.is_staff
                and getattr(usuario, "duas_etapas_ativa", False) and not verificado(request)):
            livres = (reverse("admin:duas_etapas_verificar"), reverse("admin:logout"), settings.STATIC_URL)
            if not request.path.startswith(livres):
                return redirect(f"{reverse('admin:duas_etapas_verificar')}?next={request.get_full_path()}")
        return self.get_response(request)
