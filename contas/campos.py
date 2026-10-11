from django import forms
from django.db import models

from .validadores import somente_digitos, validar_cpf


class CPFFormField(forms.CharField):
    """Campo de formulário que aceita CPF com ou sem pontuação."""

    def to_python(self, value):
        return somente_digitos(super().to_python(value))


class CPFField(models.CharField):
    """Guarda o CPF sempre só com números (11 dígitos) e valida os dígitos verificadores."""

    default_validators = [validar_cpf]

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 11)
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        value = super().to_python(value)
        return somente_digitos(value) if value else value

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        return somente_digitos(value) if value else value

    def formfield(self, **kwargs):
        kwargs.setdefault("form_class", CPFFormField)
        kwargs["max_length"] = 14  # permite digitar "000.000.000-00"
        return super().formfield(**kwargs)
