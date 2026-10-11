import re

from django.core.exceptions import ValidationError


def somente_digitos(valor):
    """'123.456.789-09' -> '12345678909'"""
    return re.sub(r"\D", "", str(valor or ""))


def cpf_e_valido(cpf):
    """Confere os dois dígitos verificadores do CPF."""
    cpf = somente_digitos(cpf)
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for posicao in (9, 10):
        soma = sum(int(cpf[n]) * (posicao + 1 - n) for n in range(posicao))
        digito = (soma * 10) % 11 % 10
        if digito != int(cpf[posicao]):
            return False
    return True


def validar_cpf(valor):
    if not cpf_e_valido(valor):
        raise ValidationError("CPF inválido. Confira os números digitados.", code="cpf_invalido")


def formatar_cpf(cpf):
    cpf = somente_digitos(cpf)
    if len(cpf) != 11:
        return cpf
    return f"{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"


def normalizar_celular(telefone):
    """'(98) 98765-4321' -> '5598987654321'. Devolve '' se não for um celular brasileiro válido."""
    digitos = somente_digitos(telefone)
    if len(digitos) in (10, 11):
        digitos = "55" + digitos
    if not digitos.startswith("55") or len(digitos) not in (12, 13):
        return ""
    if len(digitos) == 12:
        if digitos[4] not in "6789":  # 8 dígitos começando com 2-5 é telefone fixo
            return ""
        digitos = digitos[:4] + "9" + digitos[4:]
    if digitos[4] != "9":
        return ""
    return digitos


def extrair_celular(valor):
    """
    O primeiro celular válido de um campo de planilha, que às vezes traz mais de um número
    ('98 98888-7777 / 98 3222-1111', '(098) 98888-7777', '98988887777 98999991111'). Devolve '' se não achar.
    Número sem DDD NÃO é completado: um DDD errado mandaria documentos para um estranho.
    """
    candidatos = []
    for parte in re.split(r"[/;,|\n]|\s+(?:e|ou)\s+", str(valor or "")):
        digitos = somente_digitos(parte)
        if len(digitos) == 14 and digitos.startswith("0"):  # 0 + operadora + DDD + número
            digitos = digitos[3:]
        digitos = digitos.lstrip("0")
        if len(digitos) == 22:      # dois celulares colados
            candidatos += [digitos[:11], digitos[11:]]
        elif len(digitos) == 20:    # dois números de 8 dígitos com DDD colados
            candidatos += [digitos[:10], digitos[10:]]
        elif digitos:
            candidatos.append(digitos)
    for candidato in candidatos:
        celular = normalizar_celular(candidato)
        if celular:
            return celular
    return ""


def formatar_celular(celular):
    """
    '5598987654321' -> '(98) 98765-4321'. Aceita também o formato antigo, sem o 9, que o WhatsApp às vezes
    informa ('559887654321' -> '(98) 98765-4321'). Antes, esse formato virava DDD 59 e o celular se perdia.
    """
    digitos = normalizar_celular(celular)[-11:]  # '' se não for celular: devolve como veio, sem inventar DDD
    return f"({digitos[:2]}) {digitos[2:7]}-{digitos[7:]}" if len(digitos) == 11 else celular
