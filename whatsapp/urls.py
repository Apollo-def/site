from django.urls import path

from . import views

app_name = "whatsapp"

urlpatterns = [
    path("webhook/", views.webhook, name="webhook"),
    path("gateway/", views.gateway, name="gateway"),
    path("d/<str:token>/", views.documento, name="documento"),
]
