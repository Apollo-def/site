"""
Gera um backup completo (banco + PDFs) num único .zip, para levar ao servidor ou guardar.

    python manage.py exportar_backup                       -> dados_rh/backups/portal_AAAA-MM-DD_HHMM.zip
    python manage.py exportar_backup caminho/arquivo.zip
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from portal.backup import exportar


class Command(BaseCommand):
    help = "Gera um .zip com o banco de dados e todos os arquivos privados."

    def add_arguments(self, parser):
        parser.add_argument("destino", nargs="?")

    def handle(self, *args, destino=None, **options):
        destino = destino or settings.BASE_DIR / "dados_rh" / "backups" / f"portal_{timezone.localtime():%Y-%m-%d_%H%M}.zip"
        total, tamanho = exportar(destino)
        self.stdout.write(self.style.SUCCESS(f"Backup gerado: {destino} ({total} arquivos, {tamanho} MB)."))
        self.stdout.write("ATENÇÃO: contém dados pessoais de todos os funcionários. Guarde com cuidado.")
