"""
Django settings for the Operation Golden Keyboard competition backend.

The API is the single source of truth for the competition rules: nothing about
claims, ownership, deadlines or scores may depend on a client behaving well.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# SECURITY WARNING: this default is for local development only.
SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-golden-keyboard-dev-key-do-not-use-in-production",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third party
    "rest_framework",
    "rest_framework.authtoken",
    "django_filters",
    "drf_spectacular",
    # Local
    "competition",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

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
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# The scoped "claim" throttle stores its counters here. Declared explicitly so the
# throttle behaviour is identical under `runserver` and under the test runner.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "golden-keyboard-locmem",
    }
}


REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    # Page size lives here and only here. No view overrides it.
    "PAGE_SIZE": 10,
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_THROTTLE_RATES": {"claim": "5/min"},
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}


SPECTACULAR_SETTINGS = {
    "TITLE": "Operation Golden Keyboard API",
    "VERSION": "1.0.0",
    "DESCRIPTION": (
        "Backend for a campus-wide, multi-hostel coding competition.\n\n"
        "**The API is the single source of truth.** Ownership, claim availability, "
        "deadlines and scores are all enforced server-side.\n\n"
        "Key rules:\n\n"
        "* **Claiming** — `POST /api/missions/{id}/claim/` is race-safe (a single "
        "conditional `UPDATE`), throttled to 5 requests per minute per user, and "
        "ignores the request body entirely. It sets the mission to `in_progress`, "
        "records the claimant, and copies the claimant's hostel onto the mission.\n"
        "* **Ownership** — only the claimant may change a mission's status, and only "
        "the `status` field. Any other key in the payload is rejected with `403`.\n"
        "* **Deadlines** — expiry is *computed*, never stored. A mission that is "
        "`unclaimed` or `in_progress` past its deadline reads as `expired` everywhere "
        "and cannot be claimed or updated. A `cracked` mission stays cracked and keeps "
        "its points.\n"
        "* **Scores** — never stored. Hostel scores are aggregated live from cracked "
        "missions, so a staff edit that un-cracks a mission drops the score instantly.\n"
        "* **Status codes** — `401` anonymous, `403` wrong role or forbidden field, "
        "`404` missing, `400` expired or invalid transition, `409` already claimed, "
        "`429` claim rate limit."
    ),
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api",
    "SORT_OPERATIONS": False,
    "SWAGGER_UI_SETTINGS": {
        "persistAuthorization": True,
        "displayRequestDuration": True,
    },
}


# Under the test runner only: skip the deliberately-slow production password hasher.
# This affects nothing the API does -- `check_password` still works -- it just keeps
# the suite fast as it grows. Registration still runs Django's password validators.
if "test" in sys.argv:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
