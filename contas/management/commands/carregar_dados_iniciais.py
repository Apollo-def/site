"""
Cria os cadastros básicos do sistema. Pode rodar quantas vezes quiser: não duplica nada
e não sobrescreve o que o RH já tiver editado.

    python manage.py carregar_dados_iniciais
"""
from django.core.management.base import BaseCommand

from contas.models import Contrato
from orientacoes.models import Categoria
from solicitacoes.models import TipoSolicitacao

CONTRATOS = [
    ("SEMED", "Secretaria Municipal de Educação"),
    ("SEMCAS", "Secretaria Municipal da Criança e Assistência Social"),
    ("TRT-16", "Tribunal Regional do Trabalho da 16ª Região"),
    ("TJMA", "Tribunal de Justiça do Maranhão"),
]

CATEGORIAS = ["Vale-Transporte", "Vale-Alimentação", "Férias", "Documentos Pessoais", "Outros"]

TIPOS_SOLICITACAO = [
    {"nome": "Cancelamento de Vale-Transporte", "codigo": "vt_cancelar", "ordem": 1,
     "descricao": "Quando o funcionário não quer mais receber o Vale-Transporte."},
    {"nome": "Opção / Reopção de Vale-Transporte", "codigo": "vt_utilizar", "ordem": 2,
     "descricao": "Para passar a receber (ou voltar a receber) o Vale-Transporte."},
    {"nome": "Cancelamento de Vale-Alimentação", "ordem": 3,
     "descricao": "Quando o funcionário não quer mais receber o Vale-Alimentação."},
    {"nome": "Correção de dados cadastrais", "ordem": 9,
     "descricao": "Algum dado do cadastro está errado e precisa ser corrigido."},
]


class Command(BaseCommand):
    help = "Cria contratos, categorias e tipos de solicitação padrão do Portal do Colaborador."

    def handle(self, *args, **options):
        for sigla, nome in CONTRATOS:
            _, criado = Contrato.objects.get_or_create(sigla=sigla, defaults={"nome": nome})
            self._informar("Contrato", sigla, criado)

        for ordem, nome in enumerate(CATEGORIAS, start=1):
            _, criado = Categoria.objects.get_or_create(nome=nome, defaults={"ordem": ordem})
            self._informar("Categoria", nome, criado)

        for dados in TIPOS_SOLICITACAO:
            dados = dict(dados)
            nome = dados.pop("nome")
            _, criado = TipoSolicitacao.objects.get_or_create(nome=nome, defaults=dados)
            self._informar("Tipo de solicitação", nome, criado)

        self.stdout.write(self.style.SUCCESS("Dados iniciais conferidos."))

    def _informar(self, tipo, nome, criado):
        situacao = "criado" if criado else "já existia"
        self.stdout.write(f"  {tipo}: {nome} ({situacao})")
