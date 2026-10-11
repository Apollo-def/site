from django.apps import AppConfig


class ContasConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "contas"
    verbose_name = "Cadastro"

    def ready(self):
        from . import sinais  # noqa: F401  (liga a auditoria de login/saída)
