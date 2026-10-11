"""
Lê as mensagens do grupo de ocorrências (faltas, atestados, coberturas e extras) e transforma em registros.

Aceita o modelo combinado com os supervisores ("TIPO: EXTRA / DATA: 08/10 / FUNCIONÁRIO: ... / ESCOLA/POSTO: ...")
e também o jeito livre que eles já usam ("Extra hoje 08/10 / Funcionária: ... / Cobrindo Atestado Médico: ...").

O funcionário é procurado assim, do mais seguro para o menos:
  1. matrícula escrita na mensagem;
  2. nome (em qualquer ordem, incompleto e com grafia parecida: "Gercileia" acha "GERCILEA") entre quem trabalha
     na escola/posto informado;
  3. nome sozinho, só se houver UMA pessoa compatível no cadastro.
Na dúvida o sistema não chuta: a ocorrência fica "Conferir" com os candidatos para o RH escolher.
"""
import base64
import binascii
import difflib
import re
import unicodedata
from datetime import date, datetime, timedelta

from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from contas.models import Funcionario

from .models import Ocorrencia

Tipo = Ocorrencia.Tipo
# Vale a que aparece primeiro: "Extra ... cobrindo atestado" é EXTRA; "Falta ... sem cobertura" é FALTA.
SEM_COBERTURA = ("sem cobertura", "sem cobrir", "sem substituto", "sem substituicao", "nao teve cobertura",
                 "nao foi coberto", "nao foi coberta", "ninguem cobriu")
NEGACOES = SEM_COBERTURA + ("sem extra",)
PALAVRAS_TIPO = [
    (Tipo.EXTRA, ("extra",)),
    (Tipo.COBERTURA, ("cobertura", "cobrindo", "substitui", "substituicao")),
    (Tipo.ATESTADO, ("atestado",)),
    (Tipo.FALTA, ("falta", "faltou", "faltando")),
]
CAMPOS = {  # campo -> começos aceitos para o rótulo da linha ("Funcionária:", "Escola/Posto:")
    "tipo": ("tipo",),
    "data": ("data", "dia"),
    "funcionario": ("funcionari", "colaborador", "nome", "servidor", "porteir", "vigia", "agente"),
    "escola": ("escola", "posto", "local", "unidade", "instalacao", "lotacao", "ueb", "creche"),
    "cobrindo": ("cobrindo", "cobertura de", "substituindo", "no lugar de", "cobre"),
    "atestado": ("atestado", "periodo"),
    "obs": ("obs", "observ"),
}
PALAVRAS_SEM_PESO = {"DE", "DA", "DO", "DAS", "DOS", "E"}
PALAVRAS_DE_ESCOLA_SEM_PESO = PALAVRAS_SEM_PESO | {
    "ESCOLA", "UEB", "UI", "U", "I", "EMEF", "CRECHE", "ANEXO", "FUNDAMENTAL", "INFANTIL", "MUNICIPAL", "UNIDADE",
    "PROF", "PROFESSOR", "PROFESSORA", "SEMED", "POSTO", "II", "III", "IV",
}
MINUTOS_PARA_JUNTAR_FOTO = 15  # foto mandada logo depois do texto vai para a mesma ocorrência


# ---------------------------------------------------------------------------
# Texto
# ---------------------------------------------------------------------------
def consertar(texto):
    """Desfaz acentos corrompidos na importação ("OPERÃ\\x81RIA" -> "OPERÁRIA")."""
    texto = texto or ""
    if "Ã" in texto or "Â" in texto:
        try:
            return texto.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return texto
    return texto


def simplificar(texto):
    """Maiúsculo, sem acento, sem pontuação: "Escola Lindalva Teotônio!" -> "ESCOLA LINDALVA TEOTONIO"."""
    texto = unicodedata.normalize("NFKD", consertar(texto)).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9 ]+", " ", texto).split())


def palavras(texto, sem_peso=PALAVRAS_SEM_PESO):
    return [p for p in simplificar(texto).split() if p not in sem_peso and len(p) > 1]


def parecida(a, b):
    """Mesma palavra com grafia parecida: GERCILEIA ~ GERCILEA, TEOTONIO ~ TEOTONIA."""
    if a == b:
        return True
    if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True  # abreviado ("FERN" / "FERNANDES"); se sobrar mais de um candidato, o RH confere
    return min(len(a), len(b)) >= 5 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


# ---------------------------------------------------------------------------
# Separar os campos da mensagem
# ---------------------------------------------------------------------------
# Rótulo sem dois-pontos ("Funcionário Rogério Cícero", "Posto Dom José", "Dia 08/10"): só estas palavras.
ROTULOS_SEM_DOIS_PONTOS = {
    "funcionario": "funcionario", "funcionaria": "funcionario", "colaborador": "funcionario",
    "colaboradora": "funcionario", "posto": "escola", "escola": "escola", "local": "escola", "unidade": "escola",
    "dia": "data", "data": "data", "obs": "obs", "observacao": "obs", "cobrindo": "cobrindo",
}
SO_O_TIPO = {"falta", "faltas", "atestado", "atestados", "cobertura", "coberturas", "extra", "extras"}
CONTINUA_A_LINHA_DE_CIMA = ("funcionario", "obs", "cobrindo")  # nome/observação que quebrou em duas linhas


def campo_da_linha(linha):
    """(campo, valor, rótulo) da linha, ou None se ela não tiver rótulo conhecido."""
    rotulo, separador, valor = linha.partition(":")
    valor = valor.strip().strip("*_").strip()
    if separador and valor:
        rotulo_simples = simplificar(rotulo).lower()
        campo = next((c for c, comecos in CAMPOS.items() if rotulo_simples.startswith(comecos)), None)
        if campo:
            return campo, valor, rotulo
        if tipo_no_texto(rotulo_simples):
            return "funcionario", valor, rotulo  # "Falta: Maria José" / "Extra: João Silva"
        return None
    primeira, _, resto = linha.partition(" ")
    campo = ROTULOS_SEM_DOIS_PONTOS.get(simplificar(primeira).lower())
    if campo and resto.strip():
        return campo, resto.strip(" :-"), primeira
    return None


def dividir_em_blocos(texto):
    """
    Uma mensagem pode ter várias ocorrências: separa por linha em branco, ou quando começa outro "Falta"/"Extra"
    depois de uma ocorrência que já tinha funcionário.
    """
    blocos, atual = [], []
    for linha in (texto or "").splitlines():
        linha = linha.strip().strip("*_~").strip()
        if not linha:
            if atual:
                blocos.append(atual)
                atual = []
            continue
        tem_funcionario = any((campo_da_linha(l) or ("",))[0] == "funcionario" for l in atual)
        comeca_outra = simplificar(linha).lower() in SO_O_TIPO or (campo_da_linha(linha) or ("",))[0] == "funcionario"
        if comeca_outra and tem_funcionario:
            blocos.append(atual)
            atual = []
        atual.append(linha)
    if atual:
        blocos.append(atual)
    # Bloco sem funcionário ("Boa tarde!", "Sem cobertura" depois de uma linha em branco) junta com o vizinho.
    juntos = []
    for bloco in blocos:
        tem = any((campo_da_linha(l) or ("",))[0] == "funcionario" for l in bloco)
        if juntos and (not tem or not juntos[-1][1]):
            juntos[-1] = (juntos[-1][0] + bloco, juntos[-1][1] or tem)
        else:
            juntos.append((bloco, tem))
    return [bloco for bloco, _ in juntos]


def separar_campos(linhas):
    """{"tipo": ..., "funcionario": ..., ...} de um bloco + "cabecalho" (as linhas sem rótulo)."""
    if isinstance(linhas, str):
        linhas = [l.strip().strip("*_~").strip() for l in linhas.splitlines() if l.strip()]
    campos, cabecalho, ultimo = {}, [], None
    for linha in linhas:
        achado = campo_da_linha(linha)
        if achado:
            campo, valor, rotulo = achado
            rotulo_simples = simplificar(rotulo).lower()
            if campo == "funcionario" and tipo_no_texto(rotulo_simples) and not rotulo_simples.startswith(
                    CAMPOS["funcionario"]):
                campos.setdefault("tipo", rotulo)
            if campo == "cobrindo" and rotulo_simples not in ("cobrindo", "cobertura de", "substituindo",
                                                              "no lugar de"):
                campos.setdefault("motivo_cobertura", rotulo.split(" ", 1)[-1])  # "Cobrindo Atestado Médico"
            if campo not in campos:
                campos[campo] = valor
                ultimo = campo
            continue
        diz_algo = tipo_no_texto(linha) or datas_no_texto(linha, date.today()) or any(
            f" {n} " in f" {simplificar(linha).lower()} " for n in NEGACOES)
        if ultimo in CONTINUA_A_LINHA_DE_CIMA and not diz_algo:
            campos[ultimo] = f"{campos[ultimo]} {linha}"  # "Funcionário Ivan D" + "Ângelo carvalho"
        else:
            cabecalho.append(linha)
            ultimo = None
    campos["cabecalho"] = " ".join(cabecalho)
    return campos


def extrair_coberto(texto):
    """"está cobrindo férias do funcionário Jonas Pereira" -> ("Jonas Pereira", "férias")."""
    achou = re.search(
        r"cobr\w*\s+(?:o\s+|a\s+)?(?:(?P<motivo>aviso(?:\s+pr[ée]vio)?|f[ée]rias|atestado(?:\s+m[ée]dico)?|falta|folga"
        r"|licen\w+)\s+)?(?:(?:do|da|de)\s+)?(?:funcion[áa]ri[oa]\s+|colaborador[a]?\s+)?(?P<nome>[A-Za-zÀ-ÿ' ]{3,})",
        texto or "", re.IGNORECASE)
    if not achou:
        return "", ""
    return " ".join(achou.group("nome").split()), (achou.group("motivo") or "").strip()


def tipo_no_texto(texto, unico=False):
    """
    O tipo pela palavra-chave que aparece primeiro. unico=True (o campo "TIPO:"): se vier mais
    de um ("FALTA / ATESTADO / COBERTURA / EXTRA", o modelo colado sem escolher), devolve None para o RH conferir.
    """
    t = f" {simplificar(texto).lower()} "
    for negacao in NEGACOES:  # "Falta ... Sem cobertura" é FALTA
        t = t.replace(f" {negacao} ", " ")
    posicoes = {}
    for tipo, chaves in PALAVRAS_TIPO:
        achadas = [t.find(f" {chave}") for chave in chaves if f" {chave}" in t]
        if achadas:
            posicoes[tipo] = min(achadas)
    if unico and len(posicoes) > 1:
        return None
    return min(posicoes, key=posicoes.get) if posicoes else None  # o que aparece primeiro na mensagem


def datas_no_texto(texto, referencia):
    """Datas escritas (08/10, 08/10/2026, 8-10-26) e "hoje"/"ontem", na ordem em que aparecem."""
    achadas = []
    t = simplificar(texto).lower()
    for dia, mes, ano in re.findall(r"\b(\d{1,2})\s*[/.-]\s*(\d{1,2})(?:\s*[/.-]\s*(\d{2,4}))?\b", texto or ""):
        try:
            if ano:
                achada = date(int(ano) + (2000 if len(ano) == 2 else 0), int(mes), int(dia))
            else:
                achada = date(referencia.year, int(mes), int(dia))
                if achada > referencia + timedelta(days=60):
                    achada = achada.replace(year=achada.year - 1)  # "28/12" lido em janeiro é do ano passado
        except ValueError:
            continue
        achadas.append(achada)
    if not achadas and " ontem " in f" {t} ":
        achadas.append(referencia - timedelta(days=1))
    if not achadas and " hoje " in f" {t} ":
        achadas.append(referencia)
    return achadas


def matricula_no_texto(texto):
    achou = re.search(r"\bmat(?:r[ií]cula)?\.?\s*:?\s*(\d{2,10})\b", texto or "", re.IGNORECASE)
    return achou.group(1) if achou else ""


# ---------------------------------------------------------------------------
# Achar o funcionário
# ---------------------------------------------------------------------------
def candidatos_pelo_nome(nome, funcionarios):
    """Quem tem todas as palavras escritas no nome (em qualquer ordem, aceitando grafia parecida)."""
    escritas = palavras(nome)
    if len(escritas) < 2:  # só o primeiro nome não basta para ter certeza
        return []
    achados = []
    for f in funcionarios:
        do_cadastro = palavras(f.nome_completo)
        if all(any(parecida(e, c) for c in do_cadastro) for e in escritas):
            achados.append(f)
    return achados


def trabalha_na_escola(funcionario, escola):
    """A instalação do cadastro tem a maior parte das palavras da escola escrita no grupo."""
    escritas = palavras(escola, PALAVRAS_DE_ESCOLA_SEM_PESO)
    if not escritas:
        return False
    da_instalacao = palavras(funcionario.instalacao, PALAVRAS_DE_ESCOLA_SEM_PESO)
    batem = sum(1 for e in escritas if any(parecida(e, i) for i in da_instalacao))
    return batem / len(escritas) >= 0.5


def encontrar_funcionario(nome, escola="", matricula="", funcionarios=None):
    """(funcionário ou None, explicação do que conferir, candidatos)."""
    if matricula:
        achado = Funcionario.objects.filter(matricula=matricula).first()
        if achado:
            return achado, "", [achado]
    if not nome.strip():
        return None, "o nome não veio na mensagem", []
    if funcionarios is None:
        funcionarios = list(Funcionario.objects.filter(situacao=Funcionario.Situacao.ATIVO).only(
            "id", "nome_completo", "matricula", "instalacao", "contrato_id"))
    candidatos = candidatos_pelo_nome(nome, funcionarios)
    if escola and len(candidatos) > 1:
        na_escola = [f for f in candidatos if trabalha_na_escola(f, escola)]
        if len(na_escola) == 1:
            return na_escola[0], "", candidatos
        if na_escola:
            candidatos = na_escola
    if len(candidatos) == 1:
        return candidatos[0], "", candidatos
    if not candidatos:
        return None, f"ninguém no cadastro com o nome \"{nome}\"", []
    return None, f"{len(candidatos)} pessoas com o nome \"{nome}\"", candidatos


# ---------------------------------------------------------------------------
# Mensagem do grupo -> ocorrência
# ---------------------------------------------------------------------------
def separar_nome(valor):
    """"Gercileia Fontes (atestado médico)" -> ("Gercileia Fontes", "atestado médico"). Também " - " e vírgula."""
    nome = re.split(r"\s*[(,]\s*|\s+-\s+", valor, maxsplit=1)[0]
    resto = valor[len(nome):].strip(" ,-()").replace(")", "").replace("(", "")
    return nome.strip(" ."), resto.strip()


def ler_bloco(linhas, texto_inteiro, recebida_em):
    """Uma ocorrência (um bloco da mensagem). None quando não é ocorrência (bom dia, ok, conversa do grupo)."""
    campos = separar_campos(linhas)
    texto_bloco = "\n".join(linhas)
    nome, detalhe_nome = separar_nome(campos.get("funcionario", ""))
    if campos.get("tipo"):
        tipo = tipo_no_texto(campos["tipo"], unico=True)  # escolheu no modelo; colou sem escolher = conferir
    else:
        tipo = tipo_no_texto(campos["cabecalho"]) or tipo_no_texto(texto_bloco)
    # Quem foi coberto: no campo "Cobrindo:" ou numa frase ("O mesmo está cobrindo Aviso do Rogério").
    motivo_cobertura, detalhe_cobrindo = campos.get("motivo_cobertura", ""), ""
    if campos.get("cobrindo"):
        cobrindo, motivo = extrair_coberto(f"cobrindo {campos['cobrindo']}")
        detalhe_cobrindo = separar_nome(campos["cobrindo"])[1]
    elif tipo in (Tipo.COBERTURA, Tipo.EXTRA):
        cobrindo, motivo = extrair_coberto(f"{campos['cabecalho']} {campos.get('obs', '')}")
    else:
        cobrindo, motivo = "", ""
    cobrindo = " ".join(separar_nome(cobrindo)[0].split()[:6])
    if not nome or not (tipo or campos.get("tipo") or campos.get("escola") or cobrindo):
        return None
    referencia = timezone.localtime(recebida_em).date()
    datas = (datas_no_texto(campos.get("data", "") or campos["cabecalho"], referencia)
             or datas_no_texto(texto_inteiro, referencia))  # data escrita uma vez só, no começo da mensagem
    periodo = datas_no_texto(campos.get("atestado", ""), referencia)
    sem_cobertura = "Sem cobertura" if any(
        f" {n} " in f" {simplificar(texto_bloco).lower()} " for n in SEM_COBERTURA) else ""
    observacao = " / ".join(dict.fromkeys(filter(None, [
        motivo_cobertura or (motivo.capitalize() if motivo else ""), detalhe_cobrindo, detalhe_nome, sem_cobertura,
        campos.get("obs", "")])))
    return {
        "tipo": tipo or "", "data": datas[0] if datas else referencia, "nome": nome[:150],
        "escola": campos.get("escola", "")[:200], "cobrindo": cobrindo[:150],
        "atestado_inicio": periodo[0] if periodo else None,
        "atestado_fim": periodo[1] if len(periodo) > 1 else (periodo[0] if periodo else None),
        "observacao": observacao, "matricula": matricula_no_texto(texto_bloco),
        "data_escrita": bool(datas),
    }


def ler_ocorrencias(texto, recebida_em):
    """Todas as ocorrências da mensagem (pode ter várias: "Falta ... / Falta ... / Extra ...")."""
    return [lido for bloco in dividir_em_blocos(texto) if (lido := ler_bloco(bloco, texto, recebida_em))]


def ler_mensagem(texto, recebida_em):
    """A primeira ocorrência da mensagem (ou None)."""
    lidas = ler_ocorrencias(texto, recebida_em)
    return lidas[0] if lidas else None


def receber_mensagem_do_grupo(dados):
    """
    Chamado pelo gateway para cada mensagem do grupo de ocorrências. Devolve as ocorrências criadas
    (o gateway reage com 🤖 na mensagem quando a lista não está vazia).
    """
    id_externo = str(dados.get("id") or "")[:120]
    if not id_externo or Ocorrencia.objects.filter(id_externo__in=[id_externo, f"{id_externo}#1"]).exists():
        return []
    texto = str(dados.get("texto") or "")[:4000]
    segundos = int(dados.get("enviada_em") or 0)
    recebida_em = datetime.fromtimestamp(segundos, tz=timezone.get_current_timezone()) if segundos else timezone.now()
    autor, autor_nome = str(dados.get("autor") or "")[:20], str(dados.get("autor_nome") or "")[:100]
    arquivo = _arquivo(dados.get("midia"))
    lidas = ler_ocorrencias(texto, recebida_em)
    if not lidas:
        if arquivo:  # foto do atestado mandada logo depois do texto: junta na ocorrência dessa pessoa
            juntou = _juntar_foto(arquivo, autor, autor_nome, recebida_em)
            return [juntou] if juntou else []
        return []
    funcionarios = list(Funcionario.objects.filter(situacao=Funcionario.Situacao.ATIVO).only(
        "id", "nome_completo", "matricula", "instalacao", "contrato_id"))
    criadas = []
    try:
        with transaction.atomic():
            for numero, lido in enumerate(lidas, 1):
                ocorrencia = _criar(lido, funcionarios, texto, autor, autor_nome,
                                    id_externo if len(lidas) == 1 else f"{id_externo}#{numero}")
                if arquivo and numero == 1:
                    ocorrencia.anexo.save(*arquivo, save=False)
                ocorrencia.save()
                criadas.append(ocorrencia)
    except IntegrityError:  # a mesma mensagem chegou duas vezes ao mesmo tempo
        return []
    return criadas


def _criar(lido, funcionarios, texto, autor, autor_nome, id_externo):
    funcionario, conferir, _ = encontrar_funcionario(lido["nome"], lido["escola"], lido["matricula"], funcionarios)
    coberto, conferir_coberto = None, ""
    if lido["cobrindo"]:
        coberto, conferir_coberto, _ = encontrar_funcionario(lido["cobrindo"], lido["escola"], "", funcionarios)
    motivos = [m for m in (
        f"funcionário: {conferir}" if conferir else "",
        f"cobrindo: {conferir_coberto}" if conferir_coberto else "",
        "o tipo (falta, atestado, cobertura ou extra) não ficou claro" if not lido["tipo"] else "",
        "a data não veio na mensagem (foi usado o dia do envio)" if not lido["data_escrita"] else "",
    ) if m]
    return Ocorrencia(
        tipo=lido["tipo"], data=lido["data"], funcionario=funcionario, nome_informado=lido["nome"],
        escola_informada=lido["escola"], coberto=coberto, coberto_nome_informado=lido["cobrindo"],
        atestado_inicio=lido["atestado_inicio"], atestado_fim=lido["atestado_fim"], observacao=lido["observacao"],
        situacao=Ocorrencia.Situacao.CONFERIR if motivos else Ocorrencia.Situacao.OK,
        motivo_conferir="; ".join(motivos)[:300], supervisor_nome=autor_nome, supervisor_numero=autor,
        texto_original=texto, id_externo=id_externo,
    )


REACOES_DE_OK = ("👍", "✅", "👌", "✔")  # 👍 com qualquer cor de pele também vale (começa com 👍)


def receber_reacao_do_grupo(dados):
    """
    O ADM reagiu a uma mensagem do grupo: 👍 (ou ✅ 👌) marca a ocorrência como "OK do ADM"; tirar a reação desmarca.
    Só vale a reação do próprio celular do chip (de_mim): a de outras pessoas do grupo não conta.
    """
    if not dados.get("de_mim"):
        return None
    ocorrencia = Ocorrencia.objects.filter(id_externo=str(dados.get("id_reagido") or "")).first()
    if ocorrencia is None:
        return None
    emoji = str(dados.get("emoji") or "")
    if emoji.startswith(REACOES_DE_OK):
        ocorrencia.ok_adm_em, ocorrencia.ok_adm_emoji = timezone.now(), emoji[:10]
    elif not emoji:  # tirou a reação
        ocorrencia.ok_adm_em, ocorrencia.ok_adm_emoji = None, ""
    else:
        return ocorrencia  # outra reação (😮, ❤️...): não muda nada
    ocorrencia.save(update_fields=["ok_adm_em", "ok_adm_emoji"])
    return ocorrencia


ASSINATURAS = {b"%PDF-": ".pdf", b"\xff\xd8\xff": ".jpg", b"\x89PNG\r\n\x1a\n": ".png"}


def _arquivo(midia):
    """(nome, ContentFile) da foto/PDF recebido, só se for mesmo foto ou PDF."""
    if not midia:
        return None
    try:
        conteudo = base64.b64decode(midia.get("base64") or "", validate=True)
    except (binascii.Error, ValueError):
        return None
    extensao = next((ext for inicio, ext in ASSINATURAS.items() if conteudo.startswith(inicio)), None)
    if extensao is None or len(conteudo) > 10 * 1024 * 1024:
        return None
    return f"ocorrencia{extensao}", ContentFile(conteudo)


def _juntar_foto(arquivo, autor, autor_nome, recebida_em):
    recentes = Ocorrencia.objects.filter(anexo="", recebida_em__gte=recebida_em - timedelta(
        minutes=MINUTOS_PARA_JUNTAR_FOTO))
    if autor:
        recentes = recentes.filter(supervisor_numero=autor)
    elif autor_nome:
        recentes = recentes.filter(supervisor_nome=autor_nome)
    else:
        return None
    ocorrencia = recentes.order_by("-recebida_em").first()
    if ocorrencia is None:
        return None
    ocorrencia.anexo.save(*arquivo, save=False)
    ocorrencia.save(update_fields=["anexo"])
    return ocorrencia
