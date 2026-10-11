"""
Configurações do Portal do Colaborador — Grupo Global / Globaltech Brasil LTDA.

Os valores sensíveis (senha do banco, chave secreta etc.) NÃO ficam neste arquivo:
eles são lidos do arquivo `.env` (no seu computador) ou das variáveis de ambiente
do servidor. Veja o modelo em `.env.example`.
"""
import os
import sys
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(nome, padrao=False):
    valor = os.environ.get(nome)
    if valor is None:
        return padrao
    return valor.strip().lower() in ("1", "true", "sim", "yes", "on")


def env_lista(nome, padrao=""):
    return [item.strip() for item in os.environ.get(nome, padrao).split(",") if item.strip()]


# ---------------------------------------------------------------------------
# Básico
# ---------------------------------------------------------------------------
CHAVE_PADRAO_DEV = "dev-inseguro-somente-para-o-computador-local"
SECRET_KEY = os.environ.get("SECRET_KEY", CHAVE_PADRAO_DEV)
# Desligado por padrão: sem configuração, o sistema nunca mostra detalhes internos de erro.
# No computador de desenvolvimento, o .env liga (DEBUG=True).
DEBUG = env_bool("DEBUG", padrao=False)

if not DEBUG and SECRET_KEY == CHAVE_PADRAO_DEV:
    raise ImproperlyConfigured("Defina a variável SECRET_KEY no servidor antes de rodar em produção.")

ALLOWED_HOSTS = env_lista("ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_lista("CSRF_TRUSTED_ORIGINS")

# No Coolify (VPS), o endereço público é informado automaticamente pelo servidor.
EM_CONTAINER = env_bool("EM_CONTAINER")  # definido no Dockerfile (Coolify)
DOMINIOS_PUBLICOS = [
    d.split("://")[-1].split("/")[0].split(":")[0]
    for d in (env_lista("COOLIFY_FQDN") + env_lista("COOLIFY_URL")
              + env_lista("DOMINIO_PUBLICO"))  # DOMINIO_PUBLICO: PC servidor com Cloudflare Tunnel
    if d.strip()
]
for dominio in dict.fromkeys(DOMINIOS_PUBLICOS):
    ALLOWED_HOSTS.append(dominio)
    CSRF_TRUSTED_ORIGINS.append(f"https://{dominio}")

# Endereço público do portal (usado depois pelo bot de WhatsApp para mandar o link).
PORTAL_URL = os.environ.get(
    "PORTAL_URL", f"https://{DOMINIOS_PUBLICOS[0]}" if DOMINIOS_PUBLICOS else "http://127.0.0.1:8010"
)

# Endereço do painel administrativo. Trocar "admin/" por algo menos óbvio dificulta ataques.
ADMIN_URL = os.environ.get("ADMIN_URL", "admin/")

# ---------------------------------------------------------------------------
# Aplicações
# ---------------------------------------------------------------------------
INSTALLED_APPS = [
    "portal.apps.PainelAdminConfig",  # o painel do Django com o menu do RH
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Módulos do portal
    "contas",          # contratos, funcionários e logins
    "contracheques",   # upload mensal e contracheques individuais
    "documentos",      # documentos individuais (informe de rendimentos, férias...)
    "orientacoes",     # central de orientações e formulários gerais
    "solicitacoes",    # solicitações e termos
    "auditoria",       # log de auditoria (LGPD)
    "whatsapp",        # Assistente do Colaborador via WhatsApp
    "ocorrencias",     # faltas, atestados, coberturas e extras lidos do grupo dos supervisores
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "portal.seguranca.CabecalhosDeSeguranca",  # CSP e outras proteções do navegador
    "whitenoise.middleware.WhiteNoiseMiddleware",  # entrega CSS/JS quando DEBUG=False
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "contas.middleware.DuasEtapasMiddleware",  # código do celular depois da senha (RH com 2 etapas ativa)
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "portal.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "portal.wsgi.application"

# ---------------------------------------------------------------------------
# Banco de dados
# Sem DATABASE_URL -> usa SQLite (arquivo db.sqlite3).
# Com DATABASE_URL -> PostgreSQL (Neon, Coolify...). Ex. do Neon:
#   DATABASE_URL=postgresql://usuario:senha@ep-xxx.sa-east-1.aws.neon.tech/neondb?sslmode=require
# Os testes automáticos rodam sempre num SQLite temporário: nunca tocam o banco de verdade.
# ---------------------------------------------------------------------------
EM_TESTE = len(sys.argv) > 1 and sys.argv[1] == "test"
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,          # reaproveita a conexão (no Neon, evita refazer a conexão segura a cada clique)
        conn_health_checks=True,   # o Neon desliga conexões paradas; confere antes de usar
    )
}
if EM_TESTE and not env_bool("TESTAR_NO_POSTGRES"):
    # TESTAR_NO_POSTGRES=True roda os testes no PostgreSQL do DATABASE_URL (num banco "test_..." separado,
    # criado e apagado pelo próprio Django) para conferir a compatibilidade antes de mudar de banco.
    DATABASES["default"] = {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db_testes.sqlite3"}
if "-pooler." in DATABASES["default"].get("HOST", ""):
    # Endereço "pooler" do Neon (PgBouncer em modo transação) não aceita cursores do lado do servidor.
    DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True
USANDO_SQLITE = DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
if USANDO_SQLITE:
    # Aguenta mais gente ao mesmo tempo: modo WAL (leituras não esperam as gravações),
    # espera até 20 s por uma vaga em vez de dar "database is locked" na hora.
    DATABASES["default"]["OPTIONS"] = {
        "timeout": 20,
        "transaction_mode": "IMMEDIATE",
        "init_command": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;",
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Usuários e senhas
# ---------------------------------------------------------------------------
AUTH_USER_MODEL = "contas.Usuario"
AUTHENTICATION_BACKENDS = ["contas.autenticacao.BackendPortal"]
LOGIN_URL = "admin:login"

# Regras das senhas do RH.
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
    {"NAME": "contas.validadores_senha.SenhaForteValidator"},  # 10+ caracteres, misturada, sem nome da empresa
]

if EM_TESTE:  # nos testes automáticos, criptografia rápida (a de verdade é lenta de propósito)
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Sessão expira após X minutos sem uso (padrão 30).
SESSION_COOKIE_AGE = int(os.environ.get("SESSAO_MINUTOS", "30")) * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
# Nomes próprios: o navegador não separa cookies por porta, e outro sistema Django aberto em 127.0.0.1 (ex.: :8000)
# sobrescrevia o "csrftoken" do portal. Aí o próximo formulário dava "Verificação CSRF falhou".
SESSION_COOKIE_NAME = "portal_rh_sessao"
CSRF_COOKIE_NAME = "portal_rh_csrf"
CSRF_FAILURE_VIEW = "portal.views.formulario_expirado"

# A sessão é renovada a cada clique. Ela fica em arquivos no próprio servidor (um por sessão), e não no
# banco: com SQLite evita a fila de gravações; com banco na internet (Neon) evita uma ida e volta a cada clique.
# As vencidas são apagadas por "manage.py clearsessions" (backup diário e início do servidor).
SESSION_ENGINE = "portal.sessoes"  # o de arquivos do Django, com a correção para o Windows (portal/sessoes.py)
SESSION_FILE_PATH = BASE_DIR / "sessoes"
SESSION_FILE_PATH.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# E-mail (aviso ao RH de pedidos novos). Sem EMAIL_HOST, nenhum e-mail é enviado.
# ---------------------------------------------------------------------------
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", padrao=True)
EMAIL_TIMEOUT = 20
DEFAULT_FROM_EMAIL = os.environ.get("EMAIL_REMETENTE", "") or f"Portal do Colaborador <{EMAIL_HOST_USER or 'portal@localhost'}>"
EMAILS_AVISO_RH = env_lista("EMAILS_AVISO_RH")

# ---------------------------------------------------------------------------
# WhatsApp. Duas formas de conectar (WHATSAPP_CANAL):
#   "gateway" = QR code (gateway-whatsapp/, não oficial: use chip secundário, o número pode ser banido)
#   "meta"    = API oficial da Meta (WhatsApp Business Cloud API)
# Sem nenhuma configurada, só o simulador do painel funciona.
# ---------------------------------------------------------------------------
WHATSAPP_CANAL = os.environ.get("WHATSAPP_CANAL", "meta").strip().lower()
WHATSAPP_GATEWAY_URL = os.environ.get("WHATSAPP_GATEWAY_URL", "http://127.0.0.1:3005")
WHATSAPP_GATEWAY_TOKEN = os.environ.get("WHATSAPP_GATEWAY_TOKEN", "")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")                  # token permanente do app da Meta
WHATSAPP_PHONE_NUMBER_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
WHATSAPP_APP_SECRET = os.environ.get("WHATSAPP_APP_SECRET", "")        # confere a assinatura de cada aviso da Meta
WHATSAPP_VERIFY_TOKEN = os.environ.get("WHATSAPP_VERIFY_TOKEN", "")    # senha combinada ao cadastrar o webhook
WHATSAPP_API_VERSAO = os.environ.get("WHATSAPP_API_VERSAO", "v23.0")
WHATSAPP_MODELO_AVISO = os.environ.get("WHATSAPP_MODELO_AVISO", "")    # modelo aprovado p/ avisos fora das 24 h
WHATSAPP_MODELO_IDIOMA = os.environ.get("WHATSAPP_MODELO_IDIOMA", "pt_BR")

# IA (Claude, da Anthropic). Sem ANTHROPIC_API_KEY o assistente funciona só com as regras próprias.
WHATSAPP_IA_LIGADA = env_bool("WHATSAPP_IA_LIGADA", padrao=bool(os.environ.get("ANTHROPIC_API_KEY")))
WHATSAPP_IA_MODELO = os.environ.get("WHATSAPP_IA_MODELO", "claude-opus-5-5")

# ---------------------------------------------------------------------------
# Idioma e fuso horário
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "pt-br"
TIME_ZONE = "America/Fortaleza"  # mesmo horário de São Luís/MA
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Arquivos
# STATIC  = CSS/JS/imagens do layout (públicos, sem dados pessoais).
# PRIVADOS = contracheques, documentos, termos. NUNCA ficam em pasta pública:
#            só saem por uma view que confere quem está logado.
# ---------------------------------------------------------------------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Pasta dos arquivos privados. No Coolify (VPS), o Dockerfile aponta para o disco permanente (/data).
PRIVATE_MEDIA_ROOT = Path(os.environ.get("PRIVATE_MEDIA_ROOT", BASE_DIR / "arquivos_privados"))

# Senha de uso único para trazer os dados do computador para o servidor (tela /restaurar-backup/).
# Só funciona com o banco vazio; depois da restauração, a tela se desliga sozinha.
RESTAURAR_BACKUP_SENHA = os.environ.get("RESTAURAR_BACKUP_SENHA", "")

STORAGES = {
    # Todo arquivo enviado ao sistema vai para o armazenamento privado.
    "default": {"BACKEND": "portal.armazenamento.ArmazenamentoPrivado"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Separação dos contracheques em segundo plano (os testes desligam para rodar direto).
PROCESSAR_EM_SEGUNDO_PLANO = env_bool("PROCESSAR_EM_SEGUNDO_PLANO", padrao=True)

# Tamanho máximo de upload (o PDF mensal pode ser grande).
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

# ---------------------------------------------------------------------------
# Segurança em produção (ativada automaticamente quando DEBUG=False)
# ---------------------------------------------------------------------------
# Em produção o portal fica atrás de um proxy (Cloudflare, Coolify): só confiamos no IP informado por ele.
CONFIAR_CABECALHO_PROXY = env_bool("CONFIAR_CABECALHO_PROXY", padrao=not DEBUG)

if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    # O gateway do WhatsApp fala com o portal dentro do próprio PC (http://127.0.0.1), protegido pela senha dele.
    SECURE_REDIRECT_EXEMPT = [r"^whatsapp/gateway/$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_REFERRER_POLICY = "same-origin"
    X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
