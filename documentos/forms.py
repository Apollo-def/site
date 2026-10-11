from django import forms
from django.core.validators import FileExtensionValidator

from portal.lotes import validar_pdf_enviado

from .models import TIPOS_EM_LOTE, LoteDocumento

TITULOS_PADRAO = dict(TIPOS_EM_LOTE)


class EnviarDocumentosForm(forms.Form):
    arquivo = forms.FileField(label="PDF com os documentos de todos os funcionários",
                              validators=[FileExtensionValidator(["pdf"])])
    tipo = forms.ChoiceField(label="Que documento é este?", choices=TIPOS_EM_LOTE)
    titulo = forms.CharField(
        label="Título que o funcionário vai ver (opcional)", max_length=150, required=False,
        help_text="Se ficar em branco, usa o nome do documento. Ex.: Registro de empregado — atualizado em 2026.",
    )

    def clean_arquivo(self):
        return validar_pdf_enviado(self.cleaned_data["arquivo"])

    def clean(self):
        dados = super().clean()
        tipo = dados.get("tipo")
        if tipo:
            dados["titulo"] = (dados.get("titulo") or "").strip() or TITULOS_PADRAO[tipo]
            em_andamento = LoteDocumento.objects.filter(
                tipo=tipo, status__in=[LoteDocumento.Status.AGUARDANDO, LoteDocumento.Status.PROCESSANDO])
            if em_andamento.exists():
                raise forms.ValidationError("Já existe um envio deste documento sendo processado. Aguarde terminar.")
        return dados
