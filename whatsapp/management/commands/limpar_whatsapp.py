"""
Apaga o que passou do prazo de guarda (Configuração > "guardar as conversas do WhatsApp por (dias)"):
mensagens antigas (e fotos/PDFs recebidos que só existiam nelas), perguntas já resolvidas e pedidos de
cadastro já resolvidos. Pedidos de VT, termos assinados e auditoria NÃO são apagados.
Roda toda noite pelo scripts\\backup_diario.ps1.   python manage.py limpar_whatsapp
"""
from datetime import timedelta

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand
from django.utils import timezone

from contas.models import ConfiguracaoPortal
from whatsapp.models import Mensagem, PedidoCadastro, PerguntaSemResposta

PASTA_RECEBIDOS = "whatsapp/recebidos/"
DIAS_TERMO_ABANDONADO = 7  # termo recebido sem a pessoa dizer se é para utilizar ou cancelar


def apagar_termos_abandonados():
    """Fotos/PDFs de termo que nunca viraram pedido. Se a pessoa voltar depois, o assistente pede de novo."""
    if not default_storage.exists(PASTA_RECEBIDOS):
        return 0
    limite = timezone.now() - timedelta(days=DIAS_TERMO_ABANDONADO)
    apagados = 0
    for nome in default_storage.listdir(PASTA_RECEBIDOS)[1]:
        caminho = PASTA_RECEBIDOS + nome
        if default_storage.get_modified_time(caminho) < limite:
            default_storage.delete(caminho)
            apagados += 1
    return apagados


class Command(BaseCommand):
    help = "Apaga mensagens do WhatsApp mais antigas que o prazo de guarda da Configuração (LGPD)."

    def handle(self, *args, **options):
        abandonados = apagar_termos_abandonados()
        if abandonados:
            self.stdout.write(f"{abandonados} termos recebidos e não concluídos há mais de "
                              f"{DIAS_TERMO_ABANDONADO} dias apagados.")
        dias = ConfiguracaoPortal.atual().whatsapp_guardar_dias
        if not dias:
            self.stdout.write("Prazo de guarda = 0: nada é apagado.")
            return
        limite = timezone.now() - timedelta(days=dias)
        antigas = Mensagem.objects.filter(criada_em__lt=limite)
        arquivos = [c for c in antigas.exclude(anexo_caminho="").values_list("anexo_caminho", flat=True)
                    if c.startswith(PASTA_RECEBIDOS)]  # termos que viraram pedido ficam (estão em outra pasta)
        mensagens, _ = antigas.delete()
        for caminho in arquivos:
            if default_storage.exists(caminho):
                default_storage.delete(caminho)
        perguntas, _ = PerguntaSemResposta.objects.filter(resolvida=True, criada_em__lt=limite).delete()
        cadastros, _ = (PedidoCadastro.objects.exclude(situacao=PedidoCadastro.Situacao.PENDENTE)
                        .filter(criado_em__lt=limite).delete())
        self.stdout.write(f"Prazo de {dias} dias: {mensagens} mensagens, {len(arquivos)} arquivos, {perguntas} perguntas "
                          f"e {cadastros} pedidos de cadastro apagados.")
