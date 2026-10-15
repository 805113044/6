import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEBUG = os.getenv("DEBUG", "false").lower() == "true"
SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = "development-only-do-not-use-in-production"
    else:
        raise ImproperlyConfigured("生产环境必须配置独立的 SECRET_KEY。")
if not DEBUG and (len(SECRET_KEY) < 40 or SECRET_KEY.startswith("REPLACE_")):
    raise ImproperlyConfigured("生产 SECRET_KEY 须使用至少 40 字符的独立随机值。")
ALLOWED_HOSTS = [
    x.strip()
    for x in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if x.strip()
]
CSRF_TRUSTED_ORIGINS = [
    x.strip() for x in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if x.strip()
]
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "booking",
]
MIDDLEWARE = [
    "booking.middleware.PrivacyHeadersMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
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
            ]
        },
    }
]
engine = os.getenv("DB_ENGINE", "sqlite")
if engine == "sqlite":
    if not DEBUG and os.getenv("ALLOW_SQLITE", "false") != "true":
        raise ImproperlyConfigured(
            "生产环境请配置 PostgreSQL 或 MySQL；SQLite 仅用于本地开发。"
        )
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
            "OPTIONS": {"timeout": 20},
        }
    }
elif engine in ("postgresql", "mysql"):
    DATABASES = {
        "default": {
            "ENGINE": f"django.db.backends.{engine}",
            "NAME": os.environ["DB_NAME"],
            "USER": os.environ["DB_USER"],
            "PASSWORD": os.environ["DB_PASSWORD"],
            "HOST": os.environ["DB_HOST"],
            "PORT": os.getenv("DB_PORT", "5432" if engine == "postgresql" else "3306"),
            "CONN_MAX_AGE": 60,
            "CONN_HEALTH_CHECKS": True,
        }
    }
    if engine == "mysql":
        DATABASES["default"]["OPTIONS"] = {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            "isolation_level": "read committed",
        }
    if os.getenv("DB_SSL_CA"):
        if engine == "postgresql":
            DATABASES["default"]["OPTIONS"] = {
                "sslmode": "verify-full",
                "sslrootcert": os.environ["DB_SSL_CA"],
            }
        else:
            DATABASES["default"]["OPTIONS"]["ssl"] = {
                "ca": os.environ["DB_SSL_CA"],
                "check_hostname": True,
            }
else:
    raise ImproperlyConfigured("DB_ENGINE 仅支持 sqlite、postgresql、mysql。")
AUTH_USER_MODEL = "booking.User"
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
        "OPTIONS": {"min_length": 10},
    },
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "rooms"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_DOMAIN = None
CSRF_COOKIE_DOMAIN = None
SESSION_COOKIE_NAME = "classroom_session" if DEBUG else "__Host-classroom_session"
CSRF_COOKIE_NAME = "classroom_csrf" if DEBUG else "__Host-classroom_csrf"
SESSION_COOKIE_AGE = 8 * 3600
SECURE_SSL_REDIRECT = not DEBUG
# Only the loopback-bound app port may be reached through the trusted reverse proxy.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# Do not enable includeSubDomains: this app must not change the main site's policy.
SECURE_HSTS_SECONDS = 0 if DEBUG else 2592000
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
RETENTION_DAYS = min(30, max(2, int(os.getenv("RETENTION_DAYS", "21"))))
BOOKING_ADVANCE_DAYS = min(
    14, RETENTION_DAYS - 1, max(1, int(os.getenv("BOOKING_ADVANCE_DAYS", "14")))
)
CANCEL_BEFORE_MINUTES = max(0, int(os.getenv("CANCEL_BEFORE_MINUTES", "30")))
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
