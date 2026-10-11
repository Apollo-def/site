from django.contrib.admin.apps import AdminConfig


class PainelAdminConfig(AdminConfig):
    """O painel do Django com o menu e a visão geral do RH (portal/admin_site.py)."""

    default_site = "portal.admin_site.PainelAdmin"
