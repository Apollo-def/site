from django.conf import settings
from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

admin.site.site_header = "Sistema TJMA — Grupo Global"
admin.site.site_title = "Sistema TJMA"
admin.site.index_title = "Painel do RH"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("", RedirectView.as_view(url="/" + settings.ADMIN_URL)),
]
