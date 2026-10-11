"""
Restaura um backup gerado por 'exportar_backup'. Só funciona com o banco vazio.

    python manage.py importar_backup caminho/arquivo.zip
    python manage.py importar_backup caminho/arquivo.zip --sem-arquivos   (só o banco; PDFs já estão no lugar)
"""
from django.core.management.base import BaseCommand, CommandError

from portal.backup import ErroBackup, importar


class Command(BaseCommand):
    help = "Restaura banco de dados e arquivos privados a partir do .zip do backup."

    def add_arguments(self, parser):
        parser.add_argument("origem")
        parser.add_argument("--sem-arquivos", action="store_true", help="Restaura só o banco de dados.")

    def handle(self, *args, origem, sem_arquivos=False, **options):
        try:
            total = importar(origem, com_arquivos=not sem_arquivos)
        except ErroBackup as erro:
            raise CommandError(str(erro))
        self.stdout.write(self.style.SUCCESS(f"Backup restaurado ({total} arquivos)."))
