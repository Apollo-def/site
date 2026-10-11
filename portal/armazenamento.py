"""
Armazenamento PRIVADO dos arquivos (contracheques, documentos, termos).

- Os arquivos ficam na pasta `arquivos_privados/`, que NÃO é publicada na internet.
- Os nomes são aleatórios (não contêm nome nem CPF do funcionário).
- O "link" de um arquivo aponta para uma view protegida, nunca para o arquivo direto.
  Na Etapa 8 esta classe pode ser trocada por um armazenamento em nuvem privado
  sem mudar o resto do sistema.
"""
import os
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import models, transaction
from django.db.models.signals import post_delete
from django.urls import reverse
from django.utils import timezone
from django.utils.deconstruct import deconstructible


class ArmazenamentoPrivado(FileSystemStorage):
    def __init__(self, location=None, **kwargs):
        kwargs.pop("base_url", None)
        super().__init__(location=location or settings.PRIVATE_MEDIA_ROOT, base_url=None, **kwargs)

    def url(self, name):
        # Link usado apenas no painel do RH (exige login de administrador).
        # O funcionário baixa os próprios arquivos por outras views, que conferem o dono.
        return reverse("arquivo_privado_admin", kwargs={"caminho": name})


def apagar_arquivos_ao_excluir(*modelos):
    """
    Ao apagar um registro (contracheque, documento, anexo...), apaga também os arquivos dele do disco.
    Sem isso, o PDF com dados pessoais ficaria esquecido na pasta. Só apaga depois que o banco confirmar.
    """
    def apagar(sender, instance, **kwargs):
        for campo in sender._meta.concrete_fields:
            arquivo = getattr(instance, campo.name) if isinstance(campo, models.FileField) else None
            if arquivo:
                transaction.on_commit(lambda nome=arquivo.name, armazenamento=arquivo.storage: armazenamento.delete(nome))

    for modelo in modelos:
        post_delete.connect(apagar, sender=modelo, weak=False, dispatch_uid=f"apagar_arquivos_{modelo._meta.label}")


@deconstructible
class CaminhoUnico:
    """Gera o caminho do arquivo: <pasta>/<ano>/<mês>/<código aleatório>.<extensão>"""

    def __init__(self, pasta):
        self.pasta = pasta

    def __call__(self, instance, filename):
        extensao = os.path.splitext(filename)[1].lower()[:10]
        hoje = timezone.localdate()
        return f"{self.pasta}/{hoje:%Y/%m}/{uuid.uuid4().hex}{extensao}"

    def __eq__(self, other):
        return isinstance(other, CaminhoUnico) and self.pasta == other.pasta
