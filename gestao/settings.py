"""
Configurações do Gestão de Suporte e Cobrança.

Tudo que é sensível ou muda entre ambientes vem de variáveis de ambiente (arquivo .env).
Veja .env.example.
"""
import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "sim", "on")


DEBUG = env_bool("DEBUG", False)
SECRET_KEY = os.getenv("SECRET_KEY") or ("dev-insecure-key-troque-em-producao" if DEBUG else None)
if not SECRET_KEY:
    raise RuntimeError("Defina SECRET_KEY no arquivo .env")

ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

# URL pública do sistema (usada em links enviados, ex.: link de conferência do sócio)
SITE_URL = os.getenv("SITE_URL", "http://localhost:8000").rstrip("/")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
    "core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "gestao.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.globais",
            ],
        },
    },
]

WSGI_APPLICATION = "gestao.wsgi.application"

# Banco: SQLite por padrão (simples e sem manutenção); PostgreSQL em produção via DATABASE_URL.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'dados' / 'db.sqlite3'}",
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "pt-br"
TIME_ZONE = os.getenv("TIME_ZONE", "America/Sao_Paulo")
USE_I18N = True
USE_TZ = True
USE_THOUSAND_SEPARATOR = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

# Arquivos enviados (boletos, comprovantes). NUNCA servidos publicamente:
# são entregues por uma view que exige login.
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", BASE_DIR / "dados" / "arquivos"))
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", BASE_DIR / "dados" / "backups"))
BACKUP_RETENCAO_DIAS = int(os.getenv("BACKUP_RETENCAO_DIAS", "30"))

DATA_UPLOAD_MAX_MEMORY_SIZE = 30 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "painel"
LOGOUT_REDIRECT_URL = "login"

# ---------- Segurança ----------
SESSION_COOKIE_AGE = 60 * 60 * 10  # 10 horas
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    # o gateway do WhatsApp chama o sistema pela rede interna do Docker, sem HTTPS (protegido por token)
    SECURE_REDIRECT_EXEMPT = [r"^webhook/gateway/$"]
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_HSTS_SECONDS = 60 * 60 * 24 * 30
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True

# Bloqueio de login após tentativas falhas
LOGIN_MAX_TENTATIVAS = 5
LOGIN_BLOQUEIO_MINUTOS = 15

# ---------- WhatsApp ----------
# PROVEDOR: "qrcode" = WhatsApp conectado por QR Code (gateway próprio, sem custo de API)
#           "oficial" = WhatsApp Business Platform / Cloud API da Meta (pago por mensagem)
WHATSAPP_PROVEDOR = os.getenv("WHATSAPP_PROVEDOR", "qrcode").strip().lower()
GATEWAY = {
    "URL": os.getenv("GATEWAY_URL", "").rstrip("/"),
    "TOKEN": os.getenv("GATEWAY_TOKEN", ""),
}
WHATSAPP = {
    "TOKEN": os.getenv("WHATSAPP_TOKEN", ""),
    "PHONE_NUMBER_ID": os.getenv("WHATSAPP_PHONE_NUMBER_ID", ""),
    "APP_SECRET": os.getenv("WHATSAPP_APP_SECRET", ""),
    "VERIFY_TOKEN": os.getenv("WHATSAPP_VERIFY_TOKEN", ""),
    "API_VERSION": os.getenv("WHATSAPP_API_VERSION", "v21.0"),
    # Sem credenciais do provedor escolhido, o sistema funciona em MODO SIMULAÇÃO: registra tudo, não envia nada.
    "SIMULACAO": env_bool("WHATSAPP_SIMULACAO", False),
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("LOG_LEVEL", "INFO")},
}
