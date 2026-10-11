from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from . import views
from .acompanhamento import painel_acompanhamento


urlpatterns = [
    path(f"{settings.ADMIN_URL}acompanhamento/", admin.site.admin_view(painel_acompanhamento),
         name="painel_acompanhamento"),
    path(settings.ADMIN_URL, admin.site.urls),
    path("restaurar-backup/", views.restaurar_backup, name="restaurar_backup"),
    path("arquivos/rh/<path:caminho>", views.arquivo_privado_admin, name="arquivo_privado_admin"),
    path("whatsapp/", include("whatsapp.urls")),
    path("", views.inicio, name="inicio"),  # página pública: o atendimento é pelo WhatsApp
]
