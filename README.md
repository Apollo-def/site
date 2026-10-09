# Sistema TJMA — Grupo Global

Cadastro dos funcionários do contrato do **Tribunal de Justiça do Maranhão (TJMA)**:
nome, matrícula, CPF, cargo, instalação (fórum / prédio) e escala (DRTA, DRTA-S).

## Primeira vez (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env                  # depois preencha o SECRET_KEY
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
```

## Rodar

```powershell
.\.venv\Scripts\python.exe manage.py runserver 8030
```

Abra http://127.0.0.1:8030/admin/ e entre com o login criado.

## Importar a planilha de funcionários

Pelo painel: **Funcionários > Importar planilha** (deixe "Somente conferir" marcado na primeira vez).

Ou pelo terminal:

```powershell
.\.venv\Scripts\python.exe manage.py importar_planilha dados_rh\planilhas\ARQUIVO.xlsx --conferir
.\.venv\Scripts\python.exe manage.py importar_planilha dados_rh\planilhas\ARQUIVO.xlsx
```

- Obrigatórias: **Nome completo, Matrícula, CPF**.
- Opcionais: Data de nascimento, Cargo, Instalação, Escala, Telefone, Situação.
- A matrícula identifica o funcionário: se já existir, o cadastro é atualizado. Coluna opcional em branco
  não apaga o que já está cadastrado.
- Instalações novas são criadas automaticamente.

## Dados pessoais (LGPD)

Planilhas, PDFs, o banco (`db.sqlite3`) e o `.env` **nunca vão para o GitHub** (veja `.gitignore`).
Guarde os arquivos de trabalho do RH em `dados_rh/`, que também fica fora do GitHub.

## Testes

```powershell
.\.venv\Scripts\python.exe manage.py test
```
