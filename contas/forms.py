from django import forms
from django.core.validators import FileExtensionValidator

from .models import Contrato, Funcionario
from .validadores import somente_digitos

TAMANHO_MAXIMO_PLANILHA_MB = 10


class ImportarFuncionariosForm(forms.Form):
    arquivo = forms.FileField(
        label="Planilha (.xlsx, .xls ou .csv)",
        validators=[FileExtensionValidator(["xlsx", "xls", "csv"], message="Envie um arquivo Excel (.xlsx ou .xls) ou .csv.")],
    )
    contrato_padrao = forms.ModelChoiceField(
        label="Contrato padrão (para quem vier sem contrato na planilha)", required=False,
        queryset=Contrato.objects.filter(ativo=True), empty_label="— nenhum —",
    )
    somente_conferir = forms.BooleanField(
        label="Somente conferir (simulação: mostra o resultado, mas NÃO salva nada)", required=False,
    )
    criar_contratos = forms.BooleanField(
        label="Criar automaticamente os contratos que ainda não estão cadastrados", required=False,
    )

    def clean_arquivo(self):
        arquivo = self.cleaned_data["arquivo"]
        if arquivo.size > TAMANHO_MAXIMO_PLANILHA_MB * 1024 * 1024:
            raise forms.ValidationError(f"Arquivo muito grande (máximo {TAMANHO_MAXIMO_PLANILHA_MB} MB).")
        return arquivo


class AtualizarEfetivoForm(forms.Form):
    """Planilha do Efetivo: completa celular, tipo de posto e instalação de quem já está cadastrado."""

    arquivo = ImportarFuncionariosForm.base_fields["arquivo"]
    somente_conferir = forms.BooleanField(
        label="Somente conferir (simulação: mostra o resultado, mas NÃO salva nada)", required=False, initial=True,
    )
    clean_arquivo = ImportarFuncionariosForm.clean_arquivo


class IdentificarFuncionarioForm(forms.Form):
    """Acha o funcionário pela matrícula ou pelo CPF (usado para vincular páginas pendentes à mão)."""

    identificador = forms.CharField(label="Matrícula ou CPF", max_length=20)

    def clean_identificador(self):
        valor = self.cleaned_data["identificador"].strip()
        funcionario = Funcionario.objects.filter(matricula=valor).first()
        digitos = somente_digitos(valor)
        if funcionario is None and len(digitos) == 11:
            funcionario = Funcionario.objects.filter(cpf=digitos).first()
        if funcionario is None:
            raise forms.ValidationError(f"Nenhum funcionário com matrícula ou CPF '{valor}'.")
        self.funcionario = funcionario
        return valor
