from django.core.exceptions import ValidationError
from django.db import models


def somente_digitos(valor):
    return "".join(c for c in str(valor or "") if c.isdigit())


def cpf_valido(cpf):
    cpf = somente_digitos(cpf)
    if len(cpf) != 11 or len(set(cpf)) == 1:
        return False
    for tamanho in (9, 10):
        soma = sum(int(cpf[i]) * (tamanho + 1 - i) for i in range(tamanho))
        if (soma * 10) % 11 % 10 != int(cpf[tamanho]):
            return False
    return True


def validar_cpf(valor):
    if not cpf_valido(valor):
        raise ValidationError("CPF inválido.")


class Instalacao(models.Model):
    """Local de trabalho dentro do TJMA (fórum, prédio, anexo...)."""
    nome = models.CharField("nome", max_length=200, unique=True)

    class Meta:
        verbose_name = "instalação"
        verbose_name_plural = "instalações"
        ordering = ["nome"]

    def __str__(self):
        return self.nome


class Funcionario(models.Model):
    class Situacao(models.TextChoices):
        ATIVO = "ativo", "Ativo"
        INATIVO = "inativo", "Inativo"

    nome_completo = models.CharField("nome completo", max_length=200)
    matricula = models.CharField("matrícula", max_length=30, unique=True)
    cpf = models.CharField("CPF", max_length=11, unique=True, validators=[validar_cpf],
                           help_text="Somente os 11 números.")
    data_nascimento = models.DateField("data de nascimento", null=True, blank=True)
    cargo = models.CharField("cargo", max_length=120, blank=True)
    instalacao = models.ForeignKey(Instalacao, verbose_name="instalação", on_delete=models.PROTECT,
                                   null=True, blank=True, related_name="funcionarios")
    escala = models.CharField("escala", max_length=30, blank=True, help_text="Ex.: DRTA, DRTA-S")
    telefone = models.CharField("telefone / WhatsApp", max_length=20, blank=True)
    situacao = models.CharField("situação", max_length=10, choices=Situacao.choices, default=Situacao.ATIVO)
    criado_em = models.DateTimeField("cadastrado em", auto_now_add=True)
    atualizado_em = models.DateTimeField("atualizado em", auto_now=True)

    class Meta:
        verbose_name = "funcionário"
        verbose_name_plural = "funcionários"
        ordering = ["nome_completo"]

    def __str__(self):
        return f"{self.nome_completo} ({self.matricula})"

    @property
    def cpf_formatado(self):
        c = self.cpf
        return f"{c[:3]}.{c[3:6]}.{c[6:9]}-{c[9:]}" if len(c) == 11 else c
