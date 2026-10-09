"""
Importa funcionários de uma planilha .xlsx.

    python manage.py importar_planilha dados_rh\\planilhas\\ARQUIVO.xlsx --conferir   (não salva nada)
    python manage.py importar_planilha dados_rh\\planilhas\\ARQUIVO.xlsx              (salva)
"""
from django.core.management.base import BaseCommand, CommandError

from funcionarios.importacao import importar_funcionarios


class Command(BaseCommand):
    help = "Importa funcionários de uma planilha Excel (.xlsx)."

    def add_arguments(self, parser):
        parser.add_argument("arquivo")
        parser.add_argument("--conferir", action="store_true", help="Só confere, não salva nada.")

    def handle(self, *args, **opcoes):
        try:
            with open(opcoes["arquivo"], "rb") as arquivo:
                resultado = importar_funcionarios(arquivo, simulacao=opcoes["conferir"])
        except FileNotFoundError:
            raise CommandError(f"Arquivo não encontrado: {opcoes['arquivo']}")
        if resultado.erro_geral:
            raise CommandError(resultado.erro_geral)
        for linha, quem, erro in resultado.erros:
            self.stdout.write(self.style.WARNING(f"Linha {linha} ({quem}): {erro}"))
        if resultado.instalacoes_criadas:
            self.stdout.write(f"{len(resultado.instalacoes_criadas)} instalações novas.")
        self.stdout.write(self.style.SUCCESS(resultado.resumo()))
