"""
Sessões em arquivos (pasta sessoes/), com uma correção para o Windows.

No Windows, um arquivo que está sendo regravado por uma requisição fica travado por um instante. Se outra
requisição da mesma pessoa chega junto (a página do relatório se atualiza a cada 2 segundos, outra aba
aberta...), a gravação dava "Permission denied" e a tela mostrava erro. Aqui ela espera um pouco e tenta de novo.
"""
import time

from django.contrib.sessions.backends import file

TENTATIVAS = 5
ESPERA_SEGUNDOS = 0.05


class SessionStore(file.SessionStore):
    def save(self, must_create=False):
        for tentativa in range(1, TENTATIVAS + 1):
            try:
                return super().save(must_create=must_create)
            except PermissionError:
                if tentativa == TENTATIVAS:
                    raise
                time.sleep(ESPERA_SEGUNDOS * tentativa)
