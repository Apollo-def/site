from django import forms
from django.core.validators import FileExtensionValidator
from django.utils import timezone

from contas.models import Contrato
from portal.lotes import validar_pdf_enviado

from .models import MESES, LoteContracheque, TipoFolha

def mes_anterior():
    hoje = timezone.localdate()
    return (12, hoje.year - 1) if hoje.month == 1 else (hoje.month - 1, hoje.year)


class EnviarContrachequesForm(forms.Form):
    arquivo = forms.FileField(label="PDF com os contracheques", validators=[FileExtensionValidator(["pdf"])])
    mes = forms.TypedChoiceField(label="Mês", choices=MESES, coerce=int)
    ano = forms.IntegerField(label="Ano", min_value=2000, max_value=2100)
    tipo = forms.ChoiceField(label="Tipo de folha", choices=TipoFolha.choices, initial=TipoFolha.MENSAL)
    contrato = forms.ModelChoiceField(
        label="Contrato (opcional)", queryset=Contrato.objects.filter(ativo=True), required=False,
        empty_label="— vários / todos os contratos —",
    )
    substituir = forms.BooleanField(
        label="Substituir os contracheques já enviados desta competência", required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        mes, ano = mes_anterior()
        self.fields["mes"].initial = mes
        self.fields["ano"].initial = ano

    def clean_arquivo(self):
        return validar_pdf_enviado(self.cleaned_data["arquivo"])

    def clean(self):
        dados = super().clean()
        if not all(k in dados for k in ("mes", "ano", "tipo")):
            return dados
        mesmos = LoteContracheque.objects.filter(ano=dados["ano"], mes=dados["mes"], tipo=dados["tipo"],
                                                 contrato=dados.get("contrato"))
        if mesmos.filter(status__in=[LoteContracheque.Status.AGUARDANDO, LoteContracheque.Status.PROCESSANDO]).exists():
            raise forms.ValidationError("Já existe um envio desta competência sendo processado. Aguarde terminar.")
        anterior = mesmos.filter(status=LoteContracheque.Status.CONCLUIDO).first()
        if anterior and not dados.get("substituir"):
            self.add_error("substituir", (
                f"Os contracheques de {anterior.competencia} ({anterior.get_tipo_display()}) já foram enviados em "
                f"{timezone.localtime(anterior.enviado_em):%d/%m/%Y %H:%M}. Deseja substituir? "
                "Marque esta opção para confirmar."
            ))
        return dados
