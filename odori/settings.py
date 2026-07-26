import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.environ.get("SESSION_SECRET", "development-only-change-me")
DEBUG = os.environ.get("DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = [
    host for host in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if host
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "pantry",
    "recipes",
    "planning",
    "shopping",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.AbsoluteSessionExpiryMiddleware",
    "core.middleware.RequestContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
if not DEBUG:
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
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
AUTH_USER_MODEL = "core.User"
LANGUAGE_CODE = "de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"
STORAGES = {"staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_AGE = int(os.environ.get("SESSION_IDLE_SECONDS", 28800))
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
INGREDIENT_EMBEDDINGS_ENABLED = (
    os.environ.get("INGREDIENT_EMBEDDINGS_ENABLED", "false").lower() == "true"
)
INGREDIENT_SEARCH_MIN_SCORE = float(os.environ.get("INGREDIENT_SEARCH_MIN_SCORE", "0.35"))
INGREDIENT_AUTO_MATCH_MIN_SCORE = float(os.environ.get("INGREDIENT_AUTO_MATCH_MIN_SCORE", "0.84"))
AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "")
AZURE_OPENAI_EMBEDDING_TIMEOUT_SECONDS = float(
    os.environ.get("AZURE_OPENAI_EMBEDDING_TIMEOUT_SECONDS", "5")
)
AZURE_OPENAI_IMAGE_DEPLOYMENT = os.environ.get("AZURE_OPENAI_IMAGE_DEPLOYMENT", "gpt-image-2")
AZURE_OPENAI_IMAGE_API_VERSION = os.environ.get(
    "AZURE_OPENAI_IMAGE_API_VERSION", "2025-04-01-preview"
)
AZURE_OPENAI_IMAGE_TIMEOUT_SECONDS = float(
    os.environ.get("AZURE_OPENAI_IMAGE_TIMEOUT_SECONDS", "60")
)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"odori.request": {"handlers": ["console"], "level": "INFO"}},
}
