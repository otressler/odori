import os
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent.parent
for _name, _value in dotenv_values(BASE_DIR / ".env").items():
    if _value is not None:
        os.environ.setdefault(_name, _value)

DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
SECRET_KEY = os.environ.get("SESSION_SECRET")
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured("SESSION_SECRET must be set when DEBUG is false.")
    SECRET_KEY = "development-only-change-me"
ALLOWED_HOSTS = [
    host for host in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host
]
CSRF_TRUSTED_ORIGINS = [
    origin for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if origin
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "orbit",
    "core",
    "pantry",
    "recipes",
    "planning",
    "shopping",
]
MIDDLEWARE = [
    "orbit.middleware.OrbitMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "core.middleware.ActiveHouseholdMiddleware",
    "core.middleware.AbsoluteSessionExpiryMiddleware",
    "core.middleware.RequestContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "odori.urls"
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
                "core.context_processors.owner_navigation",
            ]
        },
    }
]
WSGI_APPLICATION = "odori.wsgi.application"
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60,
    )
}
ORBIT_DATABASE_URL = os.environ.get("ORBIT_DATABASE_URL", "")
if ORBIT_DATABASE_URL:
    DATABASES["orbit"] = dj_database_url.parse(ORBIT_DATABASE_URL, conn_max_age=60)
    DATABASE_ROUTERS = ["core.orbit.OrbitDatabaseRouter"]
AUTH_USER_MODEL = "core.User"
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
            "secret": os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    }
}
SOCIALACCOUNT_LOGIN_ON_GET = True
SOCIALACCOUNT_AUTO_SIGNUP = True
ACCOUNT_EMAIL_VERIFICATION = "none"
LANGUAGE_CODE = "de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "data" / "uploads"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
_default_secure_setting = "true" if not DEBUG else "false"
SECURE_SSL_REDIRECT = (
    os.environ.get("SECURE_SSL_REDIRECT", _default_secure_setting).lower() == "true"
)
SECURE_HSTS_SECONDS = int(os.environ.get("SECURE_HSTS_SECONDS", "31536000" if not DEBUG else "0"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = (
    os.environ.get("SECURE_HSTS_PRELOAD", _default_secure_setting).lower() == "true"
)
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.environ.get("DATA_UPLOAD_MAX_MEMORY_SIZE", 1024 * 1024))
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(os.environ.get("DATA_UPLOAD_MAX_NUMBER_FIELDS", 600))
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
SESSION_COOKIE_AGE = int(os.environ.get("SESSION_IDLE_SECONDS", 28800))
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
INGREDIENT_EMBEDDINGS_ENABLED = (
    os.environ.get("INGREDIENT_EMBEDDINGS_ENABLED", "false").lower() == "true"
)
INGREDIENT_SEARCH_MIN_SCORE = float(os.environ.get("INGREDIENT_SEARCH_MIN_SCORE", "0.35"))
INGREDIENT_AUTO_MATCH_MIN_SCORE = float(os.environ.get("INGREDIENT_AUTO_MATCH_MIN_SCORE", "0.84"))
CATEGORY_CLASSIFIER_MIN_SIMILARITY = float(
    os.environ.get("CATEGORY_CLASSIFIER_MIN_SIMILARITY", "0.45")
)
CATEGORY_CLASSIFIER_MIN_MARGIN = float(os.environ.get("CATEGORY_CLASSIFIER_MIN_MARGIN", "0.05"))
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "")
AZURE_OPENAI_EMBEDDING_TIMEOUT_SECONDS = float(
    os.environ.get("AZURE_OPENAI_EMBEDDING_TIMEOUT_SECONDS", "5")
)
AZURE_OPENAI_IMAGE_DEPLOYMENT = os.environ.get("AZURE_OPENAI_IMAGE_DEPLOYMENT", "gpt-image-2")
AZURE_OPENAI_PANTRY_ICON_DEPLOYMENT = os.environ.get("AZURE_OPENAI_PANTRY_ICON_DEPLOYMENT", "")
AZURE_OPENAI_PANTRY_ICON_NATIVE_TRANSPARENCY = (
    os.environ.get("AZURE_OPENAI_PANTRY_ICON_NATIVE_TRANSPARENCY", "true").lower() == "true"
)
INGREDIENT_ICON_SIZE = int(os.environ.get("INGREDIENT_ICON_SIZE", "256"))
RECIPE_THUMBNAIL_SIZE = int(os.environ.get("RECIPE_THUMBNAIL_SIZE", "512"))
AZURE_OPENAI_IMAGE_API_VERSION = os.environ.get(
    "AZURE_OPENAI_IMAGE_API_VERSION", "2025-04-01-preview"
)
AZURE_OPENAI_IMAGE_TIMEOUT_SECONDS = float(
    os.environ.get("AZURE_OPENAI_IMAGE_TIMEOUT_SECONDS", "60")
)
AZURE_OPENAI_IMAGE_MIN_INTERVAL_SECONDS = float(
    os.environ.get("AZURE_OPENAI_IMAGE_MIN_INTERVAL_SECONDS", "12")
)
RECIPE_GENERATION_ENABLED = os.environ.get("RECIPE_GENERATION_ENABLED", "false").lower() == "true"
AZURE_OPENAI_RECIPE_GENERATION_DEPLOYMENT = os.environ.get(
    "AZURE_OPENAI_RECIPE_GENERATION_DEPLOYMENT", ""
)
AZURE_OPENAI_RECIPE_GENERATION_TIMEOUT_SECONDS = float(
    os.environ.get("AZURE_OPENAI_RECIPE_GENERATION_TIMEOUT_SECONDS", "30")
)
AZURE_OPENAI_RECIPE_IMPORT_DEPLOYMENT = os.environ.get(
    "AZURE_OPENAI_RECIPE_IMPORT_DEPLOYMENT", ""
)
AZURE_OPENAI_RECIPE_IMPORT_TIMEOUT_SECONDS = float(
    os.environ.get("AZURE_OPENAI_RECIPE_IMPORT_TIMEOUT_SECONDS", "45")
)
AZURE_OPENAI_RECIPE_IMPORT_MAX_OUTPUT_TOKENS = int(
    os.environ.get("AZURE_OPENAI_RECIPE_IMPORT_MAX_OUTPUT_TOKENS", "10000")
)
AZURE_OPENAI_RECIPE_GENERATION_MAX_OUTPUT_TOKENS = int(
    os.environ.get("AZURE_OPENAI_RECIPE_GENERATION_MAX_OUTPUT_TOKENS", "10000")
)
RECIPE_GENERATION_DAILY_LIMIT = int(os.environ.get("RECIPE_GENERATION_DAILY_LIMIT", "3"))
RECOMMENDATION_CANDIDATE_LIMIT = int(os.environ.get("RECOMMENDATION_CANDIDATE_LIMIT", "100"))
WORKER_HEARTBEAT_MAX_AGE_SECONDS = int(os.environ.get("WORKER_HEARTBEAT_MAX_AGE_SECONDS", "30"))
WORKER_CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "1"))
IMPORT_JOB_LEASE_SECONDS = int(os.environ.get("IMPORT_JOB_LEASE_SECONDS", "300"))
IMPORT_JOB_RETRY_DELAY_SECONDS = int(os.environ.get("IMPORT_JOB_RETRY_DELAY_SECONDS", "30"))
ORBIT_CONFIG = {
    "ENABLED": os.environ.get("ORBIT_ENABLED", "true").lower() == "true",
    "AUTH_CHECK": "core.orbit.orbit_access_allowed",
    "STORAGE_LIMIT": int(os.environ.get("ORBIT_STORAGE_LIMIT", "5000")),
    "RECORD_REQUESTS": True,
    "RECORD_QUERIES": True,
    "RECORD_LOGS": True,
    "RECORD_EXCEPTIONS": True,
    "RECORD_DUMPS": False,
    "RECORD_COMMANDS": True,
    "RECORD_CACHE": True,
    "RECORD_MODELS": False,
    "RECORD_HTTP_CLIENT": True,
    "RECORD_MAIL": True,
    "RECORD_SIGNALS": False,
    "RECORD_JOBS": True,
    "RECORD_REDIS": True,
    "RECORD_GATES": True,
    "RECORD_TRANSACTIONS": False,
    "RECORD_STORAGE": True,
    "RECORD_LLM": False,
    "MCP_ENABLED": os.environ.get("ORBIT_MCP_ENABLED", str(DEBUG)).lower() == "true",
    "MCP_INCLUDE_PAYLOADS": False,
    "MCP_MAX_LIMIT": 50,
    "MCP_MAX_PAYLOAD_CHARS": 4000,
    "WATCHER_FAIL_SILENTLY": True,
    "SLOW_QUERY_THRESHOLD_MS": int(os.environ.get("ORBIT_SLOW_QUERY_THRESHOLD_MS", "500")),
    "IGNORE_PATHS": ["/orbit/", "/static/", "/media/", "/health/"],
    "HIDE_REQUEST_HEADERS": ["Authorization", "Cookie", "X-CSRFToken"],
    "HIDE_REQUEST_BODY_KEYS": [
        "password",
        "token",
        "secret",
        "api_key",
        "content",
        "text",
        "recipe",
        "source",
    ],
    "MASK_ALL_PAYLOADS": True,
}
if ORBIT_DATABASE_URL:
    ORBIT_CONFIG.update(
        {
            "STORAGE_BACKEND": "orbit.backends.django_db.DjangoDBBackend",
            "STORAGE_DB_ALIAS": "orbit",
        }
    )
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "core.observability.JsonFormatter"}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        }
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "odori": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
