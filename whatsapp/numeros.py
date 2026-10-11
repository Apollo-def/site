"""
Números de celular no formato do WhatsApp: só dígitos, com 55 + DDD + número (ex.: 5598987654321).

O WhatsApp às vezes informa celulares antigos sem o 9 da frente (5598 8765-4321). Por isso a
busca do funcionário compara as duas formas (com e sem o 9).
"""
from contas.validadores import normalizar_celular, somente_digitos  # noqa: F401 (normalizar_celular é usada por quem importa daqui)


def variantes(numero):
    """As formas em que o mesmo celular pode aparecer: com e sem o 9 depois do DDD."""
    normal = normalizar_celular(numero)
    if not normal:
        return set()
    return {normal, normal[:4] + normal[5:]}


def mascarar(numero):
    """Para mostrar no painel sem expor o número inteiro: 5598*****4321."""
    numero = somente_digitos(numero)
    return numero[:4] + "*****" + numero[-4:] if len(numero) > 8 else numero
