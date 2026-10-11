"""
Atualização pela planilha do Efetivo (relatório com Funcionário, Matrícula, Tipo de Posto, Fones, Instalação).

Serve para completar o cadastro de quem JÁ existe (não cria ninguém, não mexe em CPF):
- celular (o que o assistente do WhatsApp usa para reconhecer a pessoa), cargo/tipo de posto e instalação;
- a pessoa é achada pela MATRÍCULA e o nome tem que bater com o do cadastro (senão a linha é recusada:
  uma matrícula trocada na planilha mandaria os documentos de um para o celular de outro);
- telefone inválido ou sem DDD não apaga o que já estava no cadastro; vai para o relatório;
- no fim, aponta celulares repetidos no cadastro (o assistente não atende número repetido).
No modo "somente conferir", tudo é processado e desfeito no final.
"""
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Count

from portal.lotes import nomes_compativeis

from .importacao import ErroPlanilha, ler_planilha, normalizar, texto
from .models import Funcionario
from .validadores import extrair_celular, formatar_celular

COLUNAS = {
    "matricula": ["matricula", "mat", "matr", "registro", "codfuncionario", "codempregado"],
    "nome": ["funcionario", "nome", "nomecompleto", "nomedofuncionario", "colaborador", "empregado"],
    "telefone": ["fones", "fone", "telefone", "telefones", "celular", "celulares", "whatsapp", "contato", "tel"],
    "cargo": ["tipodeposto", "tipoposto", "cargo", "funcao", "cargofuncao"],
    "instalacao": ["instalacao", "instalacoes", "localdetrabalho", "local", "unidade"],
}
OBRIGATORIAS = ("matricula", "nome")


@dataclass
class ResultadoEfetivo:
    simulacao: bool = False
    total_linhas: int = 0
    atualizados: list = field(default_factory=list)       # (linha, nome, matrícula, o que mudou)
    sem_alteracao: int = 0
    nao_encontrados: list = field(default_factory=list)   # (linha, nome, matrícula)
    nome_diferente: list = field(default_factory=list)    # (linha, nome na planilha, nome no cadastro, matrícula)
    telefone_invalido: list = field(default_factory=list)  # (linha, nome, matrícula, valor da planilha)
    sem_telefone: list = field(default_factory=list)      # (linha, nome, matrícula)
    repetidos: list = field(default_factory=list)         # (celular, [nomes])
    ativos_fora_da_planilha: int = 0
    erro_geral: str = ""

    @property
    def total_sem_celular(self):
        return len(self.telefone_invalido) + len(self.sem_telefone)

    @property
    def total_recusadas(self):
        return len(self.nome_diferente) + len(self.nao_encontrados)

    def resumo(self):
        return (f"{len(self.atualizados)} atualizados, {self.sem_alteracao} sem alteração, "
                f"{len(self.telefone_invalido)} telefones inválidos, {len(self.nome_diferente)} nomes diferentes, "
                f"{len(self.nao_encontrados)} matrículas fora do cadastro, {len(self.repetidos)} celulares repetidos")


def _cabecalho(linhas):
    aceitos = {normalizar(apelido): campo for campo, apelidos in COLUNAS.items() for apelido in apelidos}
    for posicao, (_, valores) in enumerate(linhas[:15]):
        mapa = {}
        for coluna, valor in enumerate(valores):
            campo = aceitos.get(normalizar(valor))
            if campo and campo not in mapa:
                mapa[campo] = coluna
        if all(c in mapa for c in OBRIGATORIAS):
            return posicao, mapa
    raise ErroPlanilha("Não encontrei a linha de títulos. A planilha precisa ter pelo menos as colunas "
                       "Matrícula e Funcionário (nome).")


def atualizar_pelo_efetivo(arquivo, nome_arquivo, simulacao=False):
    resultado = ResultadoEfetivo(simulacao=simulacao)
    try:
        linhas = ler_planilha(arquivo, nome_arquivo)
        posicao, mapa = _cabecalho(linhas)
    except ErroPlanilha as erro:
        resultado.erro_geral = str(erro)
        return resultado

    def celula(valores, campo):
        coluna = mapa.get(campo)
        return valores[coluna] if coluna is not None and coluna < len(valores) else None

    por_matricula = {f.matricula: f for f in Funcionario.objects.all()}
    vistos = set()
    with transaction.atomic():
        for numero, valores in linhas[posicao + 1:]:
            matricula, nome = texto(celula(valores, "matricula")), texto(celula(valores, "nome"))
            if not matricula and not nome:
                continue
            resultado.total_linhas += 1
            funcionario = por_matricula.get(matricula)
            if funcionario is None:
                resultado.nao_encontrados.append((numero, nome, matricula))
                continue
            vistos.add(matricula)
            if not nomes_compativeis(nome, funcionario.nome_completo):
                resultado.nome_diferente.append((numero, nome, funcionario.nome_completo, matricula))
                continue

            mudancas = {}
            if "telefone" in mapa:
                bruto = texto(celula(valores, "telefone"))
                celular = extrair_celular(bruto)
                if celular:
                    if celular != funcionario.whatsapp:
                        mudancas["telefone"] = formatar_celular(celular)
                elif bruto:
                    resultado.telefone_invalido.append((numero, funcionario.nome_completo, matricula, bruto))
                else:
                    resultado.sem_telefone.append((numero, funcionario.nome_completo, matricula))
            for campo in ("cargo", "instalacao"):
                valor = texto(celula(valores, campo))[:Funcionario._meta.get_field(campo).max_length]
                if campo in mapa and valor and valor != getattr(funcionario, campo):
                    mudancas[campo] = valor

            if not mudancas:
                resultado.sem_alteracao += 1
                continue
            for campo, valor in mudancas.items():
                setattr(funcionario, campo, valor)
            funcionario.save(update_fields=[*mudancas, "atualizado_em"])
            nomes = ", ".join(str(Funcionario._meta.get_field(c).verbose_name) for c in mudancas)
            resultado.atualizados.append((numero, funcionario.nome_completo, matricula, nomes))

        repetidos = (Funcionario.objects.exclude(whatsapp="").values("whatsapp").annotate(total=Count("id"))
                     .filter(total__gt=1).values_list("whatsapp", flat=True))
        for celular in repetidos:
            donos = Funcionario.objects.filter(whatsapp=celular).order_by("nome_completo")
            resultado.repetidos.append((formatar_celular(celular),
                                        [f"{f.nome_completo} ({f.matricula})" for f in donos]))
        resultado.ativos_fora_da_planilha = (Funcionario.objects.filter(situacao=Funcionario.Situacao.ATIVO)
                                             .exclude(matricula__in=vistos).count())
        if simulacao:
            transaction.set_rollback(True)
    return resultado
