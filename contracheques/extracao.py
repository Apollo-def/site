"""
REGRAS DE LEITURA DO CONTRACHEQUE
=================================
Se o layout do contracheque mudar, ajuste SOMENTE este arquivo.

Layout atual (sistema de folha da Globaltech, conferido com a folha de Janeiro/2026):
  - Cada página tem UM funcionário, impresso em duas vias iguais.
  - A competência aparece como "Janeiro de 2026" e o tipo como "Folha Mensal".
  - Logo abaixo do título da tabela de identificação vem a linha do funcionário:

        Código Nome do Funcionário CBO Departamento Filial
        7013 CLARA MARIA SILVA SANTOS 422105 001 1
        ^^^^ ^^^^^^^^^^^^^^^^^^^^^^^^ ^^^^^^
        |    nome (conferência)       CBO (6 dígitos)
        código do funcionário = MATRÍCULA (chave principal)

PDF escaneado (imagem, sem texto): a página cai em "páginas não identificadas" com o
motivo "sem texto". Se isso passar a acontecer sempre, dá para ligar OCR (pytesseract).
"""
import re
from dataclasses import dataclass, field

from portal.lotes import MINIMO_CARACTERES_TEXTO, sem_acento

REGRA_MATRICULA = re.compile(
    r"C[óo]digo\s+Nome\s+do\s+Funcion[áa]rio[^\n]*\n\s*(?P<matricula>\d{1,12})\s+(?P<nome>.+?)\s+\d{6}\b",
    re.IGNORECASE,
)

MESES_POR_NOME = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
REGRA_COMPETENCIA = re.compile(r"\b(?P<mes>" + "|".join(MESES_POR_NOME) + r")\s+de\s+(?P<ano>20\d{2})\b", re.I)

# "Folha Mensal", "Folha de Férias"... -> tipo de folha do sistema (usado só para conferência)
REGRA_TIPO = re.compile(r"\bFolha\s+(?P<tipo>Mensal|de\s+F[ée]rias|Complementar|de\s+Adiantamento|13)", re.I)
TIPOS = {"mensal": "mensal", "deferias": "ferias", "complementar": "complementar",
         "deadiantamento": "adiantamento", "13": "13"}

def normalizar_matricula(matricula):
    """'007013' e '7013' são a mesma matrícula."""
    return (matricula or "").strip().lstrip("0") or "0"


@dataclass
class DadosPagina:
    tem_texto: bool = False
    matriculas: list = field(default_factory=list)   # matrículas diferentes encontradas, na ordem
    nomes: dict = field(default_factory=dict)        # matrícula -> nome lido
    mes: int = None
    ano: int = None
    tipo: str = None


def ler_pagina(texto):
    dados = DadosPagina(tem_texto=len((texto or "").strip()) >= MINIMO_CARACTERES_TEXTO)
    if not dados.tem_texto:
        return dados

    for achado in REGRA_MATRICULA.finditer(texto):
        matricula = achado.group("matricula")
        if matricula not in dados.matriculas:
            dados.matriculas.append(matricula)
            dados.nomes[matricula] = " ".join(achado.group("nome").split())

    competencia = REGRA_COMPETENCIA.search(sem_acento(texto))
    if competencia:
        dados.mes = MESES_POR_NOME[competencia.group("mes").lower()]
        dados.ano = int(competencia.group("ano"))

    tipo = REGRA_TIPO.search(texto)
    if tipo:
        dados.tipo = TIPOS.get(re.sub(r"\s", "", sem_acento(tipo.group("tipo")).lower()))
    return dados
