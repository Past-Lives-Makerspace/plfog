"""Django settings for plfog - Past Lives Makerspace."""

import os
import sys
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
# Render.com sets RENDER_EXTERNAL_HOSTNAME automatically; include it when present.
_render_hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if _render_hostname:
    ALLOWED_HOSTS.append(_render_hostname)
    print(f"[plfog] RENDER_EXTERNAL_HOSTNAME={_render_hostname} added to ALLOWED_HOSTS")
else:
    print("[plfog] RENDER_EXTERNAL_HOSTNAME not set")
print(f"[plfog] Final ALLOWED_HOSTS={ALLOWED_HOSTS}")

CSRF_TRUSTED_ORIGINS = (
    os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if os.environ.get("CSRF_TRUSTED_ORIGINS") else []
)
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Allauth needs to know about the reverse proxy to resolve client IPs for rate limiting
ALLAUTH_TRUSTED_PROXY_COUNT = int(os.environ.get("ALLAUTH_TRUSTED_PROXY_COUNT", "0"))

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
    # Lets project templates override Django's built-in form widget templates
    # (see FORM_RENDERER below and templates/django/forms/widgets/).
    "django.forms",
    # Third-party
    "allauth",
    "allauth.account",
    "django_extensions",
    "anymail",
    # Project apps
    "core",
    "hub",
    "classes",
    "membership",
    "billing",
    "airtable_sync",
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
    "hub.view_as.ViewAsMiddleware",
    "plfog.service_worker_middleware.ServiceWorkerAllowedMiddleware",
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
                "core.context_processors.registration_mode",
                "core.context_processors.app_version",
                "core.context_processors.google_analytics",
                "billing.context_processors.tab_context",
                "hub.context_processors.hub_sidebar",
            ],
        },
    },
]

WSGI_APPLICATION = "plfog.wsgi.application"

# Database
DATABASE_URL = os.environ.get("DATABASE_URL")
_IS_PYTEST = "pytest" in sys.argv[0] or "PYTEST_CURRENT_TEST" in os.environ

if DATABASE_URL:
    DATABASES = {"default": dj_database_url.parse(DATABASE_URL)}
elif _IS_PYTEST:
    # Tests run without docker by default — use local sqlite file.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": str(BASE_DIR / "db.sqlite3"),
        }
    }
else:
    from django.core.exceptions import ImproperlyConfigured

    raise ImproperlyConfigured(
        "DATABASE_URL is not set. Start the dev server with `make server` "
        "(loads .env automatically) or export DATABASE_URL yourself. "
        "Refusing to fall back to on-disk sqlite — it causes schema drift "
        "between local dev and prod."
    )

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

# Media (user uploads). Stored on Cloudflare R2 in any environment that has the
# R2 env vars configured; otherwise falls back to local FileSystemStorage so
# tests and unconfigured dev machines keep working without external dependencies.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "")
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "")
R2_PUBLIC_URL = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
_R2_READY = all([R2_ACCOUNT_ID, R2_BUCKET_NAME, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_PUBLIC_URL])

# Maximum upload size for ImageField uploads (members, guilds, classes).
MAX_UPLOAD_IMAGE_BYTES = int(os.environ.get("MAX_UPLOAD_IMAGE_BYTES", str(5 * 1024 * 1024)))  # 5 MB

if _R2_READY:
    _default_storage = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": R2_BUCKET_NAME,
            "access_key": R2_ACCESS_KEY_ID,
            "secret_key": R2_SECRET_ACCESS_KEY,
            "endpoint_url": f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            "region_name": "auto",
            "signature_version": "s3v4",
            "addressing_style": "virtual",
            # R2 does not support per-object ACLs; the bucket's Public Development URL
            # (or a custom domain) serves files publicly.
            "default_acl": None,
            "querystring_auth": False,
            "custom_domain": R2_PUBLIC_URL.replace("https://", "").replace("http://", ""),
            "url_protocol": "https:",
            "file_overwrite": False,
        },
    }
else:
    _default_storage = {"BACKEND": "django.core.files.storage.FileSystemStorage"}

STORAGES = {
    "default": _default_storage,
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Use the regular template engine for form widgets so we can override Django's
# built-in widget templates from templates/django/forms/widgets/. See
# templates/django/forms/widgets/clearable_file_input.html for the customized
# image/file upload UI used by every ImageField/FileField (admin + hub).
FORM_RENDERER = "django.forms.renderers.TemplatesSetting"

# Django WebPush settings - MUST be configured via environment variables
# In CI (CI=true), use placeholder values that will fail at push time, not at startup
if os.environ.get("CI"):  # pragma: no cover -- settings.py loads before coverage
    # CI environments: use placeholders that will fail when actually sending push
    WEBPUSH_SETTINGS = {
        "VAPID_PUBLIC_KEY": "BEl62iUYgUivxIkv69yViEuiBIa-Ib9-SkvMeAtA3LF",
        "VAPID_PRIVATE_KEY": "UUxI4O8-FbRouAf7-7OT9l1E3_5N9K1L2B3",
        "VAPID_ADMIN_EMAIL": "ci@test.example.com",
    }
else:
    # Production/build: use .get() so collectstatic succeeds at Docker build time.
    # A Django system check (core.E001) errors if these are empty on `manage.py check --deploy`.
    WEBPUSH_SETTINGS = {
        "VAPID_PUBLIC_KEY": os.environ.get("WEBPUSH_VAPID_PUBLIC_KEY", ""),
        "VAPID_PRIVATE_KEY": os.environ.get("WEBPUSH_VAPID_PRIVATE_KEY", ""),
        "VAPID_ADMIN_EMAIL": os.environ.get("WEBPUSH_VAPID_ADMIN_EMAIL", ""),
    }

# Django Sites
SITE_ID = 1

# Authentication
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

# Allauth (v65+ format)
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]
ACCOUNT_EMAIL_VERIFICATION = "none"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_SESSION_REMEMBER = True

LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/"

ACCOUNT_ADAPTER = "plfog.adapters.AdminRedirectAccountAdapter"

ACCOUNT_EMAIL_SUBJECT_PREFIX = ""
ACCOUNT_FORMS = {"request_login_code": "plfog.adapters.AutoCreateUserLoginCodeForm"}

# Login-by-code (passwordless email login)
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
ACCOUNT_LOGIN_BY_CODE_TIMEOUT = 300  # 5 minutes
ACCOUNT_LOGIN_BY_CODE_MAX_ATTEMPTS = 5

# Auto-admin: comma-separated list of email domains that get admin privileges on login.
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

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend" if DEBUG else "anymail.backends.resend.EmailBackend"

ANYMAIL = {
    "RESEND_API_KEY": os.environ.get("RESEND_API_KEY"),
}

DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "noreply@pastlives.space")

# Beta feedback — comma-delimited list of recipient email addresses
BETA_FEEDBACK_EMAILS: list[str] = [
    e.strip() for e in os.environ.get("BETA_FEEDBACK_EMAILS", "josh@plaza.codes").split(",") if e.strip()
]

# Airtable sync — bidirectional sync between Django and Airtable
AIRTABLE_API_TOKEN = os.environ.get("AIRTABLE_API_TOKEN", "")
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "")
AIRTABLE_SYNC_ENABLED = os.environ.get("AIRTABLE_SYNC_ENABLED", "false").lower() == "true"

# Stripe — billing integration
# All Stripe API credentials live in the BillingSettings DB row, configured via
# the admin Payments dashboard → Settings tab. The only env var required is the
# Fernet encryption key that protects secrets at rest.
# Generate one with:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Losing this key bricks all stored Stripe credentials.
STRIPE_FIELD_ENCRYPTION_KEY = os.environ.get("STRIPE_FIELD_ENCRYPTION_KEY", "")

# Logging — ensure tracebacks reach stderr (captured by Render/Gunicorn)
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO" if DEBUG else "WARNING",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "airtable_sync": {
            "handlers": ["console"],
            "level": "INFO" if DEBUG else "WARNING",
            "propagate": False,
        },
    },
}

# django-unfold admin theme
UNFOLD = {
    "SITE_TITLE": "FOG Admin",
    "SITE_HEADER": "FOG Admin",
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
    "DASHBOARD_CALLBACK": "plfog.dashboard.dashboard_callback",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": True,
    "THEME": "dark",
    "BORDER_RADIUS": "6px",
    "COLORS": {
        "base": {
            # Matched to hub hex colors:
            #   950 = #12121f (main bg)
            #   900 = #1a1a2e (topbar/panels)
            #   700 = #092E4C (sidebar/cards)
            "50": "oklch(95% 0.01 250)",
            "100": "oklch(90% 0.02 250)",
            "200": "oklch(80% 0.03 250)",
            "300": "oklch(65% 0.04 245)",
            "400": "oklch(50% 0.05 245)",
            "500": "oklch(40% 0.05 245)",
            "600": "oklch(33% 0.05 242)",
            "700": "oklch(25.5% 0.055 240)",
            "800": "oklch(20% 0.035 265)",
            "900": "oklch(17% 0.025 265)",
            "950": "oklch(13.5% 0.025 265)",
        },
        "primary": {
            # Tuscan Yellow #EEB44B
            "50": "oklch(97% 0.03 80)",
            "100": "oklch(94% 0.06 80)",
            "200": "oklch(90% 0.10 80)",
            "300": "oklch(85% 0.13 78)",
            "400": "oklch(82% 0.145 77)",
            "500": "oklch(80% 0.145 76)",
            "600": "oklch(72% 0.13 76)",
            "700": "oklch(60% 0.12 76)",
            "800": "oklch(48% 0.10 76)",
            "900": "oklch(38% 0.08 76)",
            "950": "oklch(28% 0.05 76)",
        },
        "font": {
            # Cream #F4EFDD / Muted #96ACBB
            "subtle-light": "oklch(72% 0.025 225)",
            "subtle-dark": "oklch(72% 0.025 225)",
            "default-light": "oklch(92% 0.015 95)",
            "default-dark": "oklch(92% 0.015 95)",
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
        "show_search": False,
        "show_all_applications": False,
        "navigation": [
            {
                "items": [
                    {
                        "title": "Manage Classes",
                        "icon": "school",
                        "link": reverse_lazy("classes:admin_classes"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Members",
                        "icon": "group",
                        "link": reverse_lazy("admin:membership_member_changelist"),
                    },
                    {
                        "title": "Site Settings",
                        "icon": "settings",
                        "link": reverse_lazy("admin:core_siteconfiguration_changelist"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Voting Dashboard",
                        "icon": "how_to_vote",
                        "link": reverse_lazy("admin:index"),
                    },
                ],
            },
            {
                "title": "Billing",
                "items": [
                    {
                        "title": "Payments",
                        "icon": "payments",
                        "link": reverse_lazy("billing_admin_dashboard"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                    {
                        "title": "Reports",
                        "icon": "assessment",
                        "link": reverse_lazy("billing_admin_reports"),
                        "permission": lambda request: request.user.is_superuser,
                    },
                ],
            },
        ],
    },
}
