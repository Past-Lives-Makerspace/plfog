"""Django settings for plfog - Past Lives Makerspace."""

import os
from pathlib import Path

import dj_database_url
import sentry_sdk
from django.templatetags.static import static
from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent

# Sentry
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment="development" if os.environ.get("DJANGO_DEBUG", "True").lower() == "true" else "production",
        traces_sample_rate=0.1,
        send_default_pii=True,
    )

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-dev-key-change-in-production")
DEBUG = os.environ.get("DJANGO_DEBUG", "True").lower() == "true"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

CSRF_TRUSTED_ORIGINS = (
    os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if os.environ.get("CSRF_TRUSTED_ORIGINS") else []
)
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INSTALLED_APPS = [
    "unfold",
    "unfold.contrib.forms",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # Third-party
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.github",
    "allauth.socialaccount.providers.discord",
    "django_extensions",
    # Project apps
    "core",
    "membership",
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
    "allauth.account.middleware.AccountMiddleware",
]

ROOT_URLCONF = "plfog.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "plfog.wsgi.application"

# Database
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL)}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(BASE_DIR / "db.sqlite3"),
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "America/Los_Angeles"
USE_I18N = True
USE_TZ = True

# Static files
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Django Sites
SITE_ID = 1

# Authentication
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# Allauth (v65+ format)
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*", "password1*", "password2*"]
ACCOUNT_EMAIL_VERIFICATION = "optional"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_SESSION_REMEMBER = True

LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

SOCIALACCOUNT_ADAPTER = "plfog.adapters.AutoAdminSocialAccountAdapter"
ACCOUNT_ADAPTER = "plfog.adapters.AdminRedirectAccountAdapter"
SOCIALACCOUNT_LOGIN_ON_GET = True

# Auto-admin: comma-separated list of email domains that get admin privileges on social login.
# Empty/unset means no auto-admin. Malformed values raise ValueError at startup.
_admin_domains_raw = os.environ.get("ADMIN_DOMAINS", "")
if _admin_domains_raw.strip():
    _parsed_domains: list[str] = []
    for _domain in _admin_domains_raw.split(","):
        _domain = _domain.strip().lower()
        if not _domain:
            raise ValueError(f"ADMIN_DOMAINS contains an empty domain entry: {_admin_domains_raw!r}")
        if "@" in _domain:
            raise ValueError(f"ADMIN_DOMAINS should contain domain names, not email addresses: {_domain!r}")
        if " " in _domain:
            raise ValueError(f"ADMIN_DOMAINS contains a domain with spaces: {_domain!r}")
        if "." not in _domain:
            raise ValueError(f"ADMIN_DOMAINS contains an invalid domain (no dot): {_domain!r}")
        _parsed_domains.append(_domain)
    ADMIN_DOMAINS: list[str] = _parsed_domains
else:
    ADMIN_DOMAINS = []

EMAIL_BACKEND = (
    "django.core.mail.backends.console.EmailBackend" if DEBUG else "django.core.mail.backends.smtp.EmailBackend"
)

# OAuth providers (APP config pattern - no Django admin SocialApp needed)
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
        "APP": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "key": "",
        },
    },
    "github": {
        "SCOPE": ["user:email"],
        "APP": {
            "client_id": os.environ.get("GITHUB_CLIENT_ID", ""),
            "secret": os.environ.get("GITHUB_CLIENT_SECRET", ""),
            "key": "",
        },
    },
    "discord": {
        "SCOPE": ["identify", "email"],
        "APP": {
            "client_id": os.environ.get("DISCORD_CLIENT_ID", ""),
            "secret": os.environ.get("DISCORD_CLIENT_SECRET", ""),
            "key": "",
        },
    },
}

# django-unfold admin theme
UNFOLD = {
    "SITE_TITLE": "Past Lives",
    "SITE_HEADER": "Past Lives",
    "SITE_SYMBOL": "camping",
    "SITE_FAVICONS": [
        {
            "rel": "icon",
            "sizes": "100x100",
            "type": "image/png",
            "href": lambda request: static("img/favicon.png"),
        },
    ],
    "SITE_LOGO": {
        "light": lambda request: static("img/favicon.png"),
        "dark": lambda request: static("img/favicon.png"),
    },
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "THEME": "dark",
    "BORDER_RADIUS": "6px",
    "COLORS": {
        "base": {
            # Deep navy scale derived from brand #092e4c
            "50": "oklch(95% 0.01 240)",
            "100": "oklch(90% 0.02 240)",
            "200": "oklch(80% 0.03 240)",
            "300": "oklch(65% 0.04 240)",
            "400": "oklch(50% 0.05 240)",
            "500": "oklch(40% 0.05 240)",
            "600": "oklch(33% 0.05 240)",
            "700": "oklch(28% 0.05 238)",
            "800": "oklch(23% 0.044 240)",
            "900": "oklch(19% 0.04 240)",
            "950": "oklch(14% 0.03 240)",
        },
        "primary": {
            # Amber/golden accent scale derived from brand #eeb44b
            "50": "oklch(97% 0.03 75)",
            "100": "oklch(94% 0.06 75)",
            "200": "oklch(90% 0.10 75)",
            "300": "oklch(85% 0.13 75)",
            "400": "oklch(80% 0.15 75)",
            "500": "oklch(75% 0.15 75)",
            "600": "oklch(65% 0.14 75)",
            "700": "oklch(55% 0.13 75)",
            "800": "oklch(45% 0.10 75)",
            "900": "oklch(38% 0.08 75)",
            "950": "oklch(28% 0.05 75)",
        },
        "font": {
            # Warm cream text for dark theme
            "subtle-light": "oklch(72% 0.03 230)",
            "subtle-dark": "oklch(72% 0.03 230)",
            "default-light": "oklch(85% 0.02 95)",
            "default-dark": "oklch(85% 0.02 95)",
            "important-light": "oklch(95% 0.02 95)",
            "important-dark": "oklch(95% 0.02 95)",
        },
    },
    "LOGIN": {
        "image": lambda request: static("img/favicon.png"),
    },
    "STYLES": [
        lambda request: static("css/unfold-custom.css"),
    ],
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        "navigation": [
            {
                "title": "Navigation",
                "items": [
                    {
                        "title": "Dashboard",
                        "icon": "dashboard",
                        "link": reverse_lazy("admin:index"),
                    },
                ],
            },
            {
                "title": "Makerspace",
                "items": [
                    {
                        "title": "Members",
                        "icon": "group",
                        "link": reverse_lazy("admin:membership_member_changelist"),
                    },
                    {
                        "title": "Membership Plans",
                        "icon": "card_membership",
                        "link": reverse_lazy("admin:membership_membershipplan_changelist"),
                    },
                    {
                        "title": "Spaces",
                        "icon": "meeting_room",
                        "link": reverse_lazy("admin:membership_space_changelist"),
                    },
                    {
                        "title": "Leases",
                        "icon": "description",
                        "link": reverse_lazy("admin:membership_lease_changelist"),
                    },
                ],
            },
        ],
    },
}
