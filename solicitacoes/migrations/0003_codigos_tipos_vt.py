from django.db import migrations

# Os tipos de VT já cadastrados (carregar_dados_iniciais) passam a ser reconhecidos pela tela de
# devolução do termo de VT. Se não existirem, a própria tela cria na primeira vez.
CODIGOS = {
    "Cancelamento de Vale-Transporte": "vt_cancelar",
    "Opção / Reopção de Vale-Transporte": "vt_utilizar",
}


def marcar(apps, schema_editor):
    TipoSolicitacao = apps.get_model("solicitacoes", "TipoSolicitacao")
    for nome, codigo in CODIGOS.items():
        TipoSolicitacao.objects.filter(nome=nome, codigo__isnull=True).update(codigo=codigo)


class Migration(migrations.Migration):
    dependencies = [("solicitacoes", "0002_tiposolicitacao_codigo")]
    operations = [migrations.RunPython(marcar, migrations.RunPython.noop)]
