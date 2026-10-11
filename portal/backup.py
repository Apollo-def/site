"""
Backup completo do portal num único .zip: banco de dados (dados.json) + arquivos privados (PDFs).

Usado para levar os dados de um computador para outro servidor (PC servidor ou VPS):
  1. No computador:  python manage.py exportar_backup
  2. No servidor:    tela /restaurar-backup/ (ou python manage.py importar_backup arquivo.zip)

A restauração só é aceita com o banco VAZIO (nenhum funcionário), para nunca misturar ou apagar dados.
"""
import os
import shutil
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.management import call_command
from django.db import transaction

# Tabelas que o próprio Django recria sozinho (ou que não fazem sentido levar).
EXCLUIR = ["contenttypes", "auth.permission", "sessions", "admin.logentry"]
PASTA_ARQUIVOS = "arquivos/"


class ErroBackup(Exception):
    pass


def exportar(destino):
    """Gera o .zip em `destino`. Retorna (quantidade de arquivos, tamanho em MB)."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    raiz = Path(settings.PRIVATE_MEDIA_ROOT)
    with tempfile.TemporaryDirectory() as pasta:
        json = Path(pasta) / "dados.json"
        # Grava sempre em UTF-8 (no Windows o padrão seria outra codificação e os acentos quebrariam no servidor).
        with open(json, "w", encoding="utf-8") as saida:
            call_command("dumpdata", exclude=EXCLUIR, natural_foreign=True, natural_primary=True,
                         stdout=saida, verbosity=0)
        total = 0
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as zip_:
            zip_.write(json, "dados.json")
            if raiz.exists():
                for arquivo in sorted(raiz.rglob("*")):
                    if arquivo.is_file():
                        zip_.write(arquivo, PASTA_ARQUIVOS + arquivo.relative_to(raiz).as_posix())
                        total += 1
    return total, round(destino.stat().st_size / 1024 / 1024, 1)


def banco_vazio():
    from contas.models import Funcionario
    return not Funcionario.objects.exists()


def importar(origem, com_arquivos=True):
    """
    Restaura o .zip (caminho ou arquivo aberto). Retorna a quantidade de arquivos restaurados.
    com_arquivos=False: só o banco (ex.: levar o banco para o Neon com os PDFs já no lugar).
    """
    if not banco_vazio():
        raise ErroBackup("O banco já tem funcionários cadastrados. A restauração só é feita num banco vazio.")
    raiz = Path(settings.PRIVATE_MEDIA_ROOT)
    try:
        zip_ = zipfile.ZipFile(origem)
    except zipfile.BadZipFile:
        raise ErroBackup("Este arquivo não é um backup .zip válido.")
    with zip_, tempfile.TemporaryDirectory() as pasta:
        nomes = zip_.namelist()
        if "dados.json" not in nomes:
            raise ErroBackup("O .zip não tem o arquivo dados.json. Gere o backup com 'exportar_backup'.")
        arquivos = [n for n in nomes if n.startswith(PASTA_ARQUIVOS) and not n.endswith("/")] if com_arquivos else []
        for nome in arquivos:  # confere antes de extrair: nada pode sair da pasta de arquivos privados
            partes = PurePosixPath(nome[len(PASTA_ARQUIVOS):]).parts
            if not partes or ".." in partes or os.path.isabs(nome[len(PASTA_ARQUIVOS):]):
                raise ErroBackup(f"Caminho inválido dentro do backup: {nome}")
        json = Path(pasta) / "dados.json"
        with zip_.open("dados.json") as entrada, open(json, "wb") as saida:
            shutil.copyfileobj(entrada, saida)
        with transaction.atomic():
            call_command("loaddata", str(json), verbosity=0)
        for nome in arquivos:
            alvo = raiz / nome[len(PASTA_ARQUIVOS):]
            alvo.parent.mkdir(parents=True, exist_ok=True)
            with zip_.open(nome) as entrada, open(alvo, "wb") as saida:
                shutil.copyfileobj(entrada, saida)
    return len(arquivos)
