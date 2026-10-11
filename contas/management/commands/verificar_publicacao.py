"""
Confere se o portal está seguro para ser aberto na internet. Usado pelos scripts/compartilhar_*.ps1.

    python manage.py verificar_publicacao
"""
import os

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

SENHAS_PROIBIDAS = ["0304", "123456", "12345678", "admin", "senha", "grupo_global", "globaltech"]


class Command(BaseCommand):
    help = "Impede abrir o portal na internet com configurações inseguras."

    def handle(self, *args, **options):
        problemas = []
        if settings.DEBUG:
            problemas.append("O modo de desenvolvimento (DEBUG) está ligado.")
        if settings.SECRET_KEY.startswith(("dev-", "troque")) or len(settings.SECRET_KEY) < 40:
            problemas.append("A SECRET_KEY do arquivo .env é fraca ou de exemplo.")
        if settings.EM_CONTAINER and not os.path.ismount(os.path.dirname(settings.PRIVATE_MEDIA_ROOT)):
            problemas.append("Falta o disco permanente no Coolify. Sem ele, contracheques e documentos SOMEM a cada "
                             "atualização. Em 'Persistent Storage', adicione um volume com destino /data.")
        if settings.EM_CONTAINER and settings.DATABASES["default"]["ENGINE"].endswith("sqlite3"):
            problemas.append("Falta o banco PostgreSQL: crie um PostgreSQL no Coolify e copie a URL dele "
                             "para a variável DATABASE_URL do portal.")
        if settings.ADMIN_URL.strip("/") in ("admin", "painel", "rh", ""):
            problemas.append("O painel do RH está num endereço óbvio (/admin/). Defina ADMIN_URL no .env com um "
                             "endereço secreto (os scripts instalar_servidor e compartilhar_ngrok criam sozinhos).")
        if settings.WHATSAPP_CANAL == "gateway" and len(settings.WHATSAPP_GATEWAY_TOKEN) < 32:
            problemas.append("A senha do gateway do WhatsApp (WHATSAPP_GATEWAY_TOKEN) é curta: use 32+ caracteres.")
        for usuario in get_user_model().objects.filter(is_staff=True, is_active=True):
            if any(usuario.check_password(senha) for senha in SENHAS_PROIBIDAS):
                problemas.append(
                    f"O login do RH '{usuario.username}' está com senha fraca/provisória. Troque com:\n"
                    f"      powershell -ExecutionPolicy Bypass -File .\\scripts\\trocar_senha_rh.ps1 -Usuario {usuario.username}"
                )
        if problemas:
            raise CommandError("NÃO é seguro abrir o portal na internet ainda:\n  - " + "\n  - ".join(problemas))
        self.stdout.write(self.style.SUCCESS("Verificação de segurança OK."))
