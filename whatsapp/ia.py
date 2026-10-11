"""
IA do assistente (Claude, da Anthropic). OPCIONAL: sem ANTHROPIC_API_KEY o assistente funciona só
com as regras de interpretacao.py e a busca simples nas orientações.

Regras de privacidade (LGPD):
- A IA nunca recebe CPF, data de nascimento, salário, nome nem PDF. Antes de cada chamada, a
  mensagem passa por `anonimizar`, que apaga números longos, datas, e-mails e telefones.
- Para dúvidas, a IA recebe só a pergunta anonimizada e os textos publicados pelo RH na Central de
  orientações, com a ordem de responder apenas com base neles ou dizer que não sabe.
Qualquer erro (sem internet, limite, recusa) devolve None e o assistente segue sem a IA.
"""
import importlib.util
import json
import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT_SEGUNDOS = 20

ACOES_IA = ["contracheque", "termo_vt", "registro", "status", "atendente", "saudacao", "agradecimento", "ajuda",
            "duvida"]

ESQUEMA_PEDIDO = {
    "type": "object",
    "properties": {
        "acao": {"type": "string", "enum": ACOES_IA},
        "mes": {"type": "integer", "description": "1 a 12, ou 0 se o funcionário não disse o mês"},
        "ano": {"type": "integer", "description": "ano com 4 dígitos, ou 0 se não disse"},
        "quantidade": {"type": "integer", "description": "quantos contracheques ('os três últimos' = 3); 1 se não disse"},
        "ultimo": {"type": "boolean", "description": "pediu o mais recente / o último"},
        "tipo_folha": {"type": "string", "enum": ["mensal", "13", "ferias", "adiantamento", "complementar"]},
        "opcao_vt": {"type": "string", "enum": ["nenhuma", "vt_utilizar", "vt_cancelar"]},
    },
    "required": ["acao", "mes", "ano", "quantidade", "ultimo", "tipo_folha", "opcao_vt"],
    "additionalProperties": False,
}

ESQUEMA_RESPOSTA = {
    "type": "object",
    "properties": {
        "sabe": {"type": "boolean", "description": "true só se os textos do RH respondem a pergunta"},
        "resposta": {"type": "string", "description": "resposta curta para WhatsApp, ou vazio se não sabe"},
    },
    "required": ["sabe", "resposta"],
    "additionalProperties": False,
}

SISTEMA_PEDIDO = (
    "Você classifica mensagens de funcionários de uma empresa de serviços terceirizados (Grupo Global) "
    "enviadas ao assistente de RH no WhatsApp. Identifique o que a pessoa quer:\n"
    "- contracheque: contracheque/holerite/comprovante de pagamento (diga mês, ano, quantidade e tipo de folha "
    "se ela disser; 'mês passado' é relativo à data de hoje informada);\n"
    "- termo_vt: termo de vale-transporte, ou pedido para utilizar/cancelar o VT (opcao_vt);\n"
    "- registro: registro/ficha de empregado;\n"
    "- status: andamento de um pedido já feito;\n"
    "- atendente: quer falar com uma pessoa do RH;\n"
    "- saudacao, agradecimento, ajuda: cumprimentos, agradecimentos, pedir o menu;\n"
    "- duvida: qualquer pergunta sobre regras, direitos, prazos ou benefícios.\n"
    "Partes como [removido] foram apagadas por privacidade; ignore-as."
)

SISTEMA_DUVIDA = (
    "Você é o Assistente do Colaborador do Grupo Global e responde dúvidas de funcionários pelo WhatsApp. "
    "Responda SOMENTE com base nos textos oficiais do RH fornecidos entre <orientacoes>. Se eles não "
    "responderem a pergunta com clareza, devolva sabe=false. Nunca invente valores, datas, prazos, regras ou "
    "direitos trabalhistas, e nunca dê opinião jurídica. Use português simples, frases curtas, sem markdown, "
    "no máximo 600 caracteres. Não cumprimente nem se despeça."
)

ESQUEMA_CONVERSA = {
    "type": "object",
    "properties": {"resposta": {"type": "string", "description": "mensagem curta para WhatsApp"}},
    "required": ["resposta"],
    "additionalProperties": False,
}

SISTEMA_CONVERSA = (
    "Você é o Assistente do Colaborador do Grupo Global (empresa de serviços terceirizados) no WhatsApp. "
    "O sistema não entendeu a última mensagem do funcionário e vai mostrar a lista de serviços logo depois "
    "da sua resposta. Escreva uma resposta simpática e curta (até 300 caracteres, português simples, sem "
    "markdown) que ajude a pessoa a seguir: se for conversa casual, responda brevemente com gentileza; se "
    "parecer um dos serviços, diga qual número da lista escolher; se for pergunta sobre regras, valores, "
    "datas, prazos ou direitos, diga que você não tem essa informação confirmada e que ela pode responder RH "
    "para falar com uma pessoa. NUNCA invente regras, valores, datas, prazos ou direitos, nunca prometa nada "
    "em nome do RH e nunca peça CPF, senha ou documentos. Partes como [removido] foram apagadas por "
    "privacidade."
)

_PADROES_SENSIVEIS = [
    r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b",            # CPF
    r"\b\d{1,2}\s*[/.-]\s*\d{1,2}\s*[/.-]\s*\d{2,4}\b",  # datas
    r"\S+@\S+",                                      # e-mail
    r"\(?\b\d{2}\)?\s*9?\d{4}-?\d{4}\b",             # telefone
    r"\b\d{5,}\b",                                   # números longos (matrícula, RG, contas)
    r"\bR\$\s*[\d.,]+",                              # valores
]


def anonimizar(texto):
    for padrao in _PADROES_SENSIVEIS:
        texto = re.sub(padrao, "[removido]", texto or "")
    return texto[:1000]


def ia_disponivel():
    if not settings.WHATSAPP_IA_LIGADA:
        return False
    return importlib.util.find_spec("anthropic") is not None  # a biblioteca da IA está instalada?


def _chamar(sistema, conteudo, esquema):
    """Uma chamada curta com saída em JSON garantida. Devolve o dict ou None."""
    import anthropic

    try:
        cliente = anthropic.Anthropic(timeout=TIMEOUT_SEGUNDOS, max_retries=1)
        resposta = cliente.beta.messages.create(
            model=settings.WHATSAPP_IA_MODELO,
            max_tokens=2048,
            system=sistema,
            messages=[{"role": "user", "content": conteudo}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": esquema}},
            # Se o modelo principal recusar, a própria API tenta outro modelo na mesma chamada.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIConnectionError:
        logger.warning("IA indisponível (sem conexão)")
        return None
    except anthropic.RateLimitError:
        logger.warning("IA indisponível (limite de uso)")
        return None
    except anthropic.APIStatusError as erro:
        logger.warning("IA respondeu erro %s: %s", erro.status_code, erro.message)
        return None
    except Exception:  # ex.: IA ligada sem chave configurada; a IA nunca pode derrubar o atendimento
        logger.exception("IA mal configurada ou com erro inesperado")
        return None
    if resposta.stop_reason == "refusal":
        return None
    texto = next((bloco.text for bloco in resposta.content if bloco.type == "text"), "")
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        logger.warning("IA devolveu JSON inválido")
        return None


def classificar(texto, hoje):
    """Interpreta a mensagem quando as regras não reconheceram. Devolve o dict do ESQUEMA_PEDIDO ou None."""
    if not ia_disponivel():
        return None
    return _chamar(SISTEMA_PEDIDO, f"Hoje é {hoje:%d/%m/%Y}.\nMensagem: {anonimizar(texto)}", ESQUEMA_PEDIDO)


def conversar(texto, servicos):
    """Resposta de conversa quando as regras não entenderam (ou None sem IA). Não traz informação de RH."""
    if not ia_disponivel():
        return None
    lista = "\n".join(f"{n} - {s}" for n, s in enumerate(servicos, 1))
    dados = _chamar(SISTEMA_CONVERSA, f"<servicos>\n{lista}\n</servicos>\n\nMensagem: {anonimizar(texto)}",
                    ESQUEMA_CONVERSA)
    resposta = (dados or {}).get("resposta", "").strip()
    return resposta[:400] or None


def responder_duvida(pergunta, orientacoes):
    """
    orientacoes: lista de (título, texto) publicados pelo RH. Devolve a resposta ou None
    (IA desligada, sem base ou a IA disse que não sabe).
    """
    if not ia_disponivel() or not orientacoes:
        return None
    base = "\n\n".join(f"<orientacao titulo=\"{titulo}\">\n{texto[:4000]}\n</orientacao>" for titulo, texto in orientacoes)
    conteudo = f"<orientacoes>\n{base}\n</orientacoes>\n\nPergunta do funcionário: {anonimizar(pergunta)}"
    dados = _chamar(SISTEMA_DUVIDA, conteudo, ESQUEMA_RESPOSTA)
    if not dados or not dados.get("sabe") or not dados.get("resposta", "").strip():
        return None
    return dados["resposta"].strip()
