"""
Entende o pedido escrito do jeito do funcionário ("o do mês passado", "quero cancelar o vale",
"os três últimos") sem precisar de IA. Quando a IA está ligada, ela só entra quando estas regras
não reconhecem a mensagem (ver atendimento.py).
"""
import difflib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date


class Acao:
    CONTRACHEQUE = "contracheque"
    TERMO_VT = "termo_vt"
    REGISTRO = "registro"
    STATUS = "status"
    ATENDENTE = "atendente"
    SAUDACAO = "saudacao"
    AGRADECIMENTO = "agradecimento"
    AJUDA = "ajuda"
    DUVIDA = "duvida"
    VT = "vt"  # falou de vale-transporte sem dizer o que quer: mostra o menu do VT
    CONFIRMACAO = "confirmacao"  # "ok", "blz", "só isso", 👍: encerra com gentileza, sem repetir o menu
    FARDAMENTO = "fardamento"  # camisa, calça, bota...: pergunta as peças e os tamanhos


@dataclass
class Pedido:
    acao: str
    mes: int = None
    ano: int = None
    quantidade: int = 1
    ultimo: bool = False
    tipo_folha: str = None      # None = folha mensal; "13", "ferias", "adiantamento", "complementar"
    opcao_vt: str = None        # "vt_utilizar" / "vt_cancelar"


MESES = {"janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
         "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
MESES_CURTOS = {"jan": 1, "fev": 2, "abr": 4, "mai": 5, "jun": 6, "jul": 7, "ago": 8, "set": 9, "nov": 11, "dez": 12}
NUMEROS_ESCRITOS = {"dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6}

PALAVRAS_CONTRACHEQUE = ("contracheque", "contra cheque", "holerite", "olerite", "holerith", "demonstrativo",
                         "comprovante de pagamento", "recibo de pagamento", "folha de pagamento", "contracheques")
PALAVRAS_VT = ("vale transporte", "valetransporte", "vale de transporte", "passagem", "passagens")
PALAVRAS_CANCELAR = ("cancelar", "cancelamento", "cancela", "tirar o", "retirar", "nao quero", "nao vou usar",
                     "parar de receber", "nao utilizar", "nao usar", "desistir")
PALAVRAS_UTILIZAR = ("utilizar", "quero receber", "voltar a receber", "optar", "opcao", "reopcao", "incluir",
                     "quero o vale", "quero vt", "quero usar", "solicitar o vale", "pedir o vale", "usar o vale")
PALAVRAS_REGISTRO = ("registro de empregado", "ficha de registro", "registro", "ficha do empregado")
PALAVRAS_STATUS = ("status", "andamento", "meu pedido", "minha solicitacao", "protocolo", "situacao do",
                   "como esta", "ja foi aprovado", "foi aprovado", "resposta do rh", "ja respond")
PALAVRAS_ATENDENTE = ("atendente", "falar com alguem", "falar com uma pessoa", "falar com o rh", "falar com rh",
                      "humano", "pessoa de verdade", "quero falar", "suporte")
PALAVRAS_AGRADECIMENTO = ("obrigad", "valeu", "grato", "grata", "agradeco", "brigado")
PALAVRAS_AJUDA = ("ajuda", "menu", "opcoes", "o que voce faz", "o que vc faz", "como funciona", "inicio", "voltar")
PEDIDOS_RH = ("rh", "o rh", "falar rh", "chamar rh", "pessoa", "atendimento")
SAUDACOES = ("oi", "ola", "bom dia", "boa tarde", "boa noite", "eai", "e ai", "opa", "oie", "salve")
# Mensagem feita só destas palavras = a pessoa concordou/encerrou ("ok", "ta bom", "beleza, entendi", "só isso").
PALAVRAS_CONFIRMACAO = {"ok", "okay", "okk", "okey", "blz", "beleza", "certo", "certinho", "ta", "to", "bom", "entendi",
                        "entendido", "show", "joia", "perfeito", "combinado", "otimo", "top", "massa", "tranquilo",
                        "so", "isso", "era", "e", "de", "boa", "tudo", "ah", "aham", "uhum", "hum", "nao", "n",
                        "pode", "deixa", "fechou", "vlw"}
# Palavra inteira (não começo de palavra): "bota" não pode pegar "botar".
PALAVRAS_FARDAMENTO = {"fardamento", "farda", "fardas", "uniforme", "uniformes", "camisa", "camisas", "camiseta",
                       "calca", "calcas", "bota", "botas", "botina", "botinas", "sapato", "sapatos", "coturno"}
PEDIU_MAIS = {"sim", "s", "quero", "tenho", "preciso", "mais"}  # "sim" para "Posso ajudar em mais alguma coisa?"
# Palavras-chave que as pessoas costumam escrever errado ("contraxeque", "holerit", "tranporte").
VOCABULARIO = ("contracheque", "contracheques", "holerite", "demonstrativo", "comprovante", "pagamento",
               "transporte", "cancelar", "cancelamento", "utilizar", "registro", "andamento", "protocolo",
               "atendente", "obrigado", "obrigada", "ferias", "adiantamento", "complementar", "fardamento",
               "uniforme", "camisa", "botina", "janeiro", "fevereiro",
               "abril", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro")


def normalizar(texto):
    """Minúsculo, sem acento, sem pontuação (mantém / e números), espaços simples."""
    texto = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode().lower()
    texto = re.sub(r"[^a-z0-9/ ]+", " ", texto.replace("º", "o").replace("°", "o"))
    return f" {' '.join(texto.split())} "


def corrigir_digitacao(texto_normalizado):
    """Troca palavras quase iguais às do VOCABULARIO pela forma certa ("contraxeque" -> "contracheque")."""
    palavras = []
    for palavra in texto_normalizado.split():
        if len(palavra) >= 5 and not palavra.isdigit() and palavra not in VOCABULARIO:
            parecida = difflib.get_close_matches(palavra, VOCABULARIO, n=1, cutoff=0.8)
            palavra = parecida[0] if parecida else palavra
        palavras.append(palavra)
    return f" {' '.join(palavras)} "


def _tem(texto, palavras):
    """Alguma das palavras/expressões aparece começando uma palavra do texto ("cancela" acha "cancelar")."""
    return any(f" {p}" in texto for p in palavras)


def _mes_anterior(hoje):
    return (12, hoje.year - 1) if hoje.month == 1 else (hoje.month - 1, hoje.year)


def extrair_competencia(texto, hoje):
    """Devolve (mes, ano, quantidade, ultimo, tipo_folha) do que está escrito; None onde não diz."""
    mes = ano = None
    quantidade, ultimo, tipo_folha = 1, False, None

    if re.search(r" (13|13o|decimo terceiro|decimo 3|13 salario) ", texto):
        tipo_folha = "13"
    elif " ferias " in texto:
        tipo_folha = "ferias"
    elif " adiantamento " in texto:
        tipo_folha = "adiantamento"
    elif " complementar " in texto:
        tipo_folha = "complementar"

    achou = re.search(r" (\d{1,2})\s*/\s*(\d{2}|\d{4}) ", texto)
    if achou and 1 <= int(achou.group(1)) <= 12:
        mes, ano = int(achou.group(1)), int(achou.group(2))
        ano += 2000 if ano < 100 else 0
    else:
        for nome, numero in {**MESES, **MESES_CURTOS}.items():
            if f" {nome} " in texto:
                mes = numero
                break
        if mes:
            achou_ano = re.search(r" (20\d{2}) ", texto)
            ano = int(achou_ano.group(1)) if achou_ano else (hoje.year if mes <= hoje.month else hoje.year - 1)
        elif re.search(r" mes (passado|anterior) ", texto) or " ultimo mes " in texto:
            mes, ano = _mes_anterior(hoje)
        elif re.search(r" (este|esse|desse|deste) mes | mes atual ", texto):
            mes, ano = hoje.month, hoje.year

    achou = re.search(r" (\d|dois|duas|tres|quatro|cinco|seis) ultimos? | ultimos? (\d|dois|duas|tres|quatro|cinco|seis) ",
                      texto)
    if achou:
        valor = achou.group(1) or achou.group(2)
        quantidade = min(int(valor) if valor.isdigit() else NUMEROS_ESCRITOS[valor], 6)
        ultimo = True
        mes = ano = None
    elif mes is None and re.search(r" (ultimo|mais recente|atual|novo|recente) ", texto):
        ultimo = True
    return mes, ano, quantidade, ultimo, tipo_folha


def opcao_vt(texto):
    """'vt_cancelar', 'vt_utilizar' ou None. Aceita também a resposta curta 1/2, sim/não."""
    texto = normalizar(texto)
    if _tem(texto, PALAVRAS_CANCELAR) or texto.strip() in ("2", "cancelar", "nao"):
        return "vt_cancelar"
    if _tem(texto, PALAVRAS_UTILIZAR) or texto.strip() in ("1", "utilizar", "usar", "sim", "quero"):
        return "vt_utilizar"
    return None


def interpretar(texto, hoje=None):
    hoje = hoje or date.today()
    if (texto or "").strip() and not normalizar(texto).strip() and "?" not in texto:
        return Pedido(Acao.CONFIRMACAO)  # só emoji/pontuação: 👍 🙏 👌
    t = corrigir_digitacao(normalizar(texto))
    if set(t.split()) <= PALAVRAS_CONFIRMACAO and "?" not in texto:
        return Pedido(Acao.CONFIRMACAO)
    if set(t.split()) <= PEDIU_MAIS | {"eu", "por", "favor", "pf", "pfv"} and len(t.split()) <= 3:
        return Pedido(Acao.AJUDA)
    mes, ano, quantidade, ultimo, tipo_folha = extrair_competencia(t, hoje)
    fala_de_vt = _tem(t, PALAVRAS_VT) or " vt " in t or " vale " in t

    if _tem(t, PALAVRAS_ATENDENTE) or t.strip() in PEDIDOS_RH:
        return Pedido(Acao.ATENDENTE)
    if _tem(t, PALAVRAS_STATUS):
        return Pedido(Acao.STATUS)
    if _tem(t, PALAVRAS_CONTRACHEQUE) or (tipo_folha in ("13", "ferias", "adiantamento") and " recibo " in t):
        return Pedido(Acao.CONTRACHEQUE, mes, ano, quantidade, ultimo, tipo_folha)
    if fala_de_vt and (" termo " in t or _tem(t, PALAVRAS_CANCELAR) or _tem(t, PALAVRAS_UTILIZAR)):
        return Pedido(Acao.TERMO_VT, opcao_vt=opcao_vt(t))
    if t.strip() in ("termo", "o termo", "meu termo", "termo de opcao"):
        return Pedido(Acao.TERMO_VT)
    if fala_de_vt and len(t.split()) <= 4 and "?" not in texto:
        return Pedido(Acao.VT)  # "vt", "vale transporte", "sobre o vale": pergunta o que a pessoa quer
    if set(t.split()) & PALAVRAS_FARDAMENTO:
        return Pedido(Acao.FARDAMENTO)
    if _tem(t, PALAVRAS_REGISTRO):
        return Pedido(Acao.REGISTRO)
    if _tem(t, PALAVRAS_AGRADECIMENTO) and len(t.split()) <= 6:
        return Pedido(Acao.AGRADECIMENTO)
    if _tem(t, PALAVRAS_AJUDA):
        return Pedido(Acao.AJUDA)
    sem_saudacao = t
    for saudacao in sorted(SAUDACOES, key=len, reverse=True):
        sem_saudacao = sem_saudacao.replace(f" {saudacao} ", " ")
    if not sem_saudacao.strip() or sem_saudacao.strip() in ("tudo bem", "tudo bom", "td bem"):
        return Pedido(Acao.SAUDACAO)
    if (mes or ultimo) and len(t.split()) <= 5:
        # Só o mês ("agosto", "o do mês passado"): quase sempre é o contracheque.
        return Pedido(Acao.CONTRACHEQUE, mes, ano, quantidade, ultimo, tipo_folha)
    return Pedido(Acao.DUVIDA)
