"""BDD-style tests for plfog.settings module branches."""

import importlib
from unittest.mock import patch

import pytest

from django.test import RequestFactory


def _reload_settings(monkeypatch, env_overrides=None):
    """Helper: set env vars and reload the settings module.

    Returns the reloaded settings module with fresh attribute values.
    """
    # Clear any cached env reads by setting overrides
    if env_overrides:
        for key, value in env_overrides.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)

    from plfog import settings as settings_module

    importlib.reload(settings_module)
    return settings_module


def describe_sentry_dsn():
    def describe_when_sentry_dsn_is_set():
        def it_calls_sentry_init_with_dsn(monkeypatch):
            with patch("sentry_sdk.init") as mock_init:
                settings_module = _reload_settings(
                    monkeypatch,
                    {
                        "SENTRY_DSN": "https://examplePublicKey@o0.ingest.sentry.io/0",
                        "DJANGO_DEBUG": "True",
                    },
                )
                mock_init.assert_called_once()
                call_kwargs = mock_init.call_args[1]
                assert call_kwargs["dsn"] == "https://examplePublicKey@o0.ingest.sentry.io/0"
                assert settings_module.SENTRY_DSN == "https://examplePublicKey@o0.ingest.sentry.io/0"

        def it_sets_environment_to_development_when_debug_true(monkeypatch):
            with patch("sentry_sdk.init") as mock_init:
                _reload_settings(
                    monkeypatch,
                    {
                        "SENTRY_DSN": "https://examplePublicKey@o0.ingest.sentry.io/0",
                        "DJANGO_DEBUG": "True",
                    },
                )
                call_kwargs = mock_init.call_args[1]
                assert call_kwargs["environment"] == "development"

        def it_sets_environment_to_production_when_debug_false(monkeypatch):
            with patch("sentry_sdk.init") as mock_init:
                _reload_settings(
                    monkeypatch,
                    {
                        "SENTRY_DSN": "https://examplePublicKey@o0.ingest.sentry.io/0",
                        "DJANGO_DEBUG": "False",
                        "DJANGO_SECRET_KEY": "test-secret-key-for-production",
                        "DJANGO_ALLOWED_HOSTS": "example.com",
                    },
                )
                call_kwargs = mock_init.call_args[1]
                assert call_kwargs["environment"] == "production"

        def it_sets_traces_sample_rate(monkeypatch):
            with patch("sentry_sdk.init") as mock_init:
                _reload_settings(
                    monkeypatch,
                    {
                        "SENTRY_DSN": "https://examplePublicKey@o0.ingest.sentry.io/0",
                        "DJANGO_DEBUG": "True",
                    },
                )
                call_kwargs = mock_init.call_args[1]
                assert call_kwargs["traces_sample_rate"] == 0.1

        def it_enables_send_default_pii(monkeypatch):
            with patch("sentry_sdk.init") as mock_init:
                _reload_settings(
                    monkeypatch,
                    {
                        "SENTRY_DSN": "https://examplePublicKey@o0.ingest.sentry.io/0",
                        "DJANGO_DEBUG": "True",
                    },
                )
                call_kwargs = mock_init.call_args[1]
                assert call_kwargs["send_default_pii"] is True

    def describe_when_sentry_dsn_is_unset():
        def it_does_not_call_sentry_init(monkeypatch):
            with patch("sentry_sdk.init") as mock_init:
                _reload_settings(monkeypatch, {"SENTRY_DSN": None, "DJANGO_DEBUG": "True"})
                mock_init.assert_not_called()

        def it_sets_sentry_dsn_to_empty_string(monkeypatch):
            with patch("sentry_sdk.init"):
                settings_module = _reload_settings(monkeypatch, {"SENTRY_DSN": None, "DJANGO_DEBUG": "True"})
                assert settings_module.SENTRY_DSN == ""


def describe_database_url():
    def describe_when_database_url_is_set():
        def it_uses_dj_database_url_parse(monkeypatch):
            with patch("sentry_sdk.init"):
                settings_module = _reload_settings(
                    monkeypatch,
                    {
                        "DATABASE_URL": "postgres://user:pass@localhost/dbname",
                        "DJANGO_DEBUG": "True",
                        "SENTRY_DSN": None,
                    },
                )
                db_config = settings_module.DATABASES["default"]
                assert db_config["ENGINE"] == "django.db.backends.postgresql"
                assert db_config["NAME"] == "dbname"

    def describe_when_database_url_is_unset():
        def it_falls_back_to_sqlite(monkeypatch):
            with patch("sentry_sdk.init"):
                settings_module = _reload_settings(
                    monkeypatch,
                    {
                        "DATABASE_URL": None,
                        "DJANGO_DEBUG": "True",
                        "SENTRY_DSN": None,
                    },
                )
                db_config = settings_module.DATABASES["default"]
                assert db_config["ENGINE"] == "django.db.backends.sqlite3"
                assert str(db_config["NAME"]).endswith("db.sqlite3")


def describe_csrf_trusted_origins():
    def describe_when_env_is_set():
        def it_splits_comma_separated_origins(monkeypatch):
            with patch("sentry_sdk.init"):
                settings_module = _reload_settings(
                    monkeypatch,
                    {
                        "CSRF_TRUSTED_ORIGINS": "https://example.com,https://other.com",
                        "DJANGO_DEBUG": "True",
                        "SENTRY_DSN": None,
                    },
                )
                assert settings_module.CSRF_TRUSTED_ORIGINS == [
                    "https://example.com",
                    "https://other.com",
                ]

        def it_handles_single_origin(monkeypatch):
            with patch("sentry_sdk.init"):
                settings_module = _reload_settings(
                    monkeypatch,
                    {
                        "CSRF_TRUSTED_ORIGINS": "https://example.com",
                        "DJANGO_DEBUG": "True",
                        "SENTRY_DSN": None,
                    },
                )
                assert settings_module.CSRF_TRUSTED_ORIGINS == ["https://example.com"]

    def describe_when_env_is_unset():
        def it_returns_empty_list(monkeypatch):
            with patch("sentry_sdk.init"):
                settings_module = _reload_settings(
                    monkeypatch,
                    {
                        "CSRF_TRUSTED_ORIGINS": None,
                        "DJANGO_DEBUG": "True",
                        "SENTRY_DSN": None,
                    },
                )
                assert settings_module.CSRF_TRUSTED_ORIGINS == []


def describe_debug_false():
    def it_sets_csrf_cookie_secure_true(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {
                    "DJANGO_DEBUG": "False",
                    "DJANGO_SECRET_KEY": "test-secret-key-for-production",
                    "DJANGO_ALLOWED_HOSTS": "example.com",
                    "SENTRY_DSN": None,
                },
            )
            assert settings_module.CSRF_COOKIE_SECURE is True

    def it_sets_session_cookie_secure_true(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {
                    "DJANGO_DEBUG": "False",
                    "DJANGO_SECRET_KEY": "test-secret-key-for-production",
                    "DJANGO_ALLOWED_HOSTS": "example.com",
                    "SENTRY_DSN": None,
                },
            )
            assert settings_module.SESSION_COOKIE_SECURE is True

    def it_uses_smtp_email_backend(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {
                    "DJANGO_DEBUG": "False",
                    "DJANGO_SECRET_KEY": "test-secret-key-for-production",
                    "DJANGO_ALLOWED_HOSTS": "example.com",
                    "SENTRY_DSN": None,
                },
            )
            assert settings_module.EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend"

    def it_sets_debug_to_false(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {
                    "DJANGO_DEBUG": "False",
                    "DJANGO_SECRET_KEY": "test-secret-key-for-production",
                    "DJANGO_ALLOWED_HOSTS": "example.com",
                    "SENTRY_DSN": None,
                },
            )
            assert settings_module.DEBUG is False


def describe_debug_true():
    def it_sets_csrf_cookie_secure_false(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"DJANGO_DEBUG": "True", "SENTRY_DSN": None},
            )
            assert settings_module.CSRF_COOKIE_SECURE is False

    def it_sets_session_cookie_secure_false(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"DJANGO_DEBUG": "True", "SENTRY_DSN": None},
            )
            assert settings_module.SESSION_COOKIE_SECURE is False

    def it_uses_console_email_backend(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"DJANGO_DEBUG": "True", "SENTRY_DSN": None},
            )
            assert settings_module.EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend"

    def it_sets_debug_to_true(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"DJANGO_DEBUG": "True", "SENTRY_DSN": None},
            )
            assert settings_module.DEBUG is True


def describe_debug_default():
    def it_defaults_to_debug_true_when_env_not_set(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"DJANGO_DEBUG": None, "SENTRY_DSN": None},
            )
            assert settings_module.DEBUG is True


def describe_secret_key():
    def it_uses_env_var_when_set(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {
                    "DJANGO_SECRET_KEY": "my-custom-secret",
                    "DJANGO_DEBUG": "True",
                    "SENTRY_DSN": None,
                },
            )
            assert settings_module.SECRET_KEY == "my-custom-secret"

    def it_uses_insecure_default_when_unset(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {
                    "DJANGO_SECRET_KEY": None,
                    "DJANGO_DEBUG": "True",
                    "SENTRY_DSN": None,
                },
            )
            assert settings_module.SECRET_KEY == "django-insecure-dev-key-change-in-production"


def describe_allowed_hosts():
    def it_splits_comma_separated_hosts(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {
                    "DJANGO_ALLOWED_HOSTS": "example.com,api.example.com",
                    "DJANGO_DEBUG": "True",
                    "SENTRY_DSN": None,
                },
            )
            assert settings_module.ALLOWED_HOSTS == ["example.com", "api.example.com"]

    def it_defaults_to_localhost(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {
                    "DJANGO_ALLOWED_HOSTS": None,
                    "DJANGO_DEBUG": "True",
                    "SENTRY_DSN": None,
                },
            )
            assert settings_module.ALLOWED_HOSTS == ["localhost", "127.0.0.1"]


def describe_secure_proxy_ssl_header():
    def it_is_always_set(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"DJANGO_DEBUG": "True", "SENTRY_DSN": None},
            )
            assert settings_module.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")


def _get_unfold(monkeypatch):
    """Helper: reload settings and return the UNFOLD config dict."""
    with patch("sentry_sdk.init"):
        settings_module = _reload_settings(monkeypatch, {"DJANGO_DEBUG": "True", "SENTRY_DSN": None})
    return settings_module.UNFOLD


def describe_unfold_theme():
    def it_uses_dark_theme(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        assert unfold["THEME"] == "dark"

    def it_uses_6px_border_radius(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        assert unfold["BORDER_RADIUS"] == "6px"


def describe_unfold_site_identity():
    def it_sets_site_title_to_past_lives(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        assert unfold["SITE_TITLE"] == "Past Lives"

    def it_sets_site_header_to_past_lives(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        assert unfold["SITE_HEADER"] == "Past Lives"

    def it_sets_site_symbol(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        assert unfold["SITE_SYMBOL"] == "camping"


def describe_unfold_colors():
    def it_has_base_color_scale(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        expected_keys = {"50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"}
        assert set(unfold["COLORS"]["base"].keys()) == expected_keys

    def it_has_primary_color_scale(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        expected_keys = {"50", "100", "200", "300", "400", "500", "600", "700", "800", "900", "950"}
        assert set(unfold["COLORS"]["primary"].keys()) == expected_keys

    def it_has_font_color_keys(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        expected_keys = {
            "subtle-light",
            "subtle-dark",
            "default-light",
            "default-dark",
            "important-light",
            "important-dark",
        }
        assert set(unfold["COLORS"]["font"].keys()) == expected_keys


def describe_unfold_site_logo():
    def it_resolves_light_logo_to_favicon_path(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        request = RequestFactory().get("/")
        path = unfold["SITE_LOGO"]["light"](request)
        assert "img/favicon.png" in path

    def it_resolves_dark_logo_to_favicon_path(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        request = RequestFactory().get("/")
        path = unfold["SITE_LOGO"]["dark"](request)
        assert "img/favicon.png" in path


def describe_unfold_site_favicons():
    def it_has_one_favicon_entry(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        assert len(unfold["SITE_FAVICONS"]) == 1

    def it_resolves_favicon_href_to_favicon_path(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        request = RequestFactory().get("/")
        href = unfold["SITE_FAVICONS"][0]["href"](request)
        assert "img/favicon.png" in href


def describe_unfold_login():
    def it_has_login_key(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        assert "LOGIN" in unfold

    def it_has_callable_image(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        assert callable(unfold["LOGIN"]["image"])

    def it_resolves_image_to_favicon_path(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        request = RequestFactory().get("/")
        path = unfold["LOGIN"]["image"](request)
        assert "favicon.png" in path


def describe_unfold_styles():
    def it_resolves_custom_css_path(monkeypatch):
        unfold = _get_unfold(monkeypatch)
        request = RequestFactory().get("/")
        css_path = unfold["STYLES"][0](request)
        assert "css/unfold-custom.css" in css_path


def describe_admin_domains_empty():
    def it_returns_empty_list_when_env_is_unset(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"ADMIN_DOMAINS": None, "DJANGO_DEBUG": "True", "SENTRY_DSN": None},
            )
            assert settings_module.ADMIN_DOMAINS == []

    def it_returns_empty_list_when_env_is_empty_string(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"ADMIN_DOMAINS": "", "DJANGO_DEBUG": "True", "SENTRY_DSN": None},
            )
            assert settings_module.ADMIN_DOMAINS == []

    def it_returns_empty_list_when_env_is_whitespace_only(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"ADMIN_DOMAINS": "   ", "DJANGO_DEBUG": "True", "SENTRY_DSN": None},
            )
            assert settings_module.ADMIN_DOMAINS == []


def describe_admin_domains_valid():
    def it_returns_single_domain(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"ADMIN_DOMAINS": "example.com", "DJANGO_DEBUG": "True", "SENTRY_DSN": None},
            )
            assert settings_module.ADMIN_DOMAINS == ["example.com"]

    def it_returns_multiple_domains(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {
                    "ADMIN_DOMAINS": "pastlives.space,roaming-panda.com",
                    "DJANGO_DEBUG": "True",
                    "SENTRY_DSN": None,
                },
            )
            assert settings_module.ADMIN_DOMAINS == ["pastlives.space", "roaming-panda.com"]

    def it_strips_whitespace_around_domains(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {
                    "ADMIN_DOMAINS": " pastlives.space , roaming-panda.com ",
                    "DJANGO_DEBUG": "True",
                    "SENTRY_DSN": None,
                },
            )
            assert settings_module.ADMIN_DOMAINS == ["pastlives.space", "roaming-panda.com"]

    def it_lowercases_domains(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"ADMIN_DOMAINS": "PASTLIVES.SPACE,Roaming-Panda.COM", "DJANGO_DEBUG": "True", "SENTRY_DSN": None},
            )
            assert settings_module.ADMIN_DOMAINS == ["pastlives.space", "roaming-panda.com"]


def describe_admin_domains_validation():
    def it_raises_for_email_address(monkeypatch):
        with patch("sentry_sdk.init"):
            with pytest.raises(ValueError, match="not email addresses"):
                _reload_settings(
                    monkeypatch,
                    {"ADMIN_DOMAINS": "user@example.com", "DJANGO_DEBUG": "True", "SENTRY_DSN": None},
                )

    def it_raises_for_domain_with_spaces(monkeypatch):
        with patch("sentry_sdk.init"):
            with pytest.raises(ValueError, match="spaces"):
                _reload_settings(
                    monkeypatch,
                    {"ADMIN_DOMAINS": "past lives.space", "DJANGO_DEBUG": "True", "SENTRY_DSN": None},
                )

    def it_raises_for_trailing_comma(monkeypatch):
        with patch("sentry_sdk.init"):
            with pytest.raises(ValueError, match="empty domain entry"):
                _reload_settings(
                    monkeypatch,
                    {"ADMIN_DOMAINS": "example.com,", "DJANGO_DEBUG": "True", "SENTRY_DSN": None},
                )

    def it_raises_for_domain_without_dot(monkeypatch):
        with patch("sentry_sdk.init"):
            with pytest.raises(ValueError, match="no dot"):
                _reload_settings(
                    monkeypatch,
                    {"ADMIN_DOMAINS": "localhost", "DJANGO_DEBUG": "True", "SENTRY_DSN": None},
                )


def describe_socialaccount_adapter_setting():
    def it_points_to_auto_admin_adapter(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"DJANGO_DEBUG": "True", "SENTRY_DSN": None},
            )
            assert settings_module.SOCIALACCOUNT_ADAPTER == "plfog.adapters.AutoAdminSocialAccountAdapter"


def describe_account_adapter_setting():
    def it_points_to_admin_redirect_adapter(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"DJANGO_DEBUG": "True", "SENTRY_DSN": None},
            )
            assert settings_module.ACCOUNT_ADAPTER == "plfog.adapters.AdminRedirectAccountAdapter"


def describe_socialaccount_login_on_get():
    def it_is_enabled(monkeypatch):
        with patch("sentry_sdk.init"):
            settings_module = _reload_settings(
                monkeypatch,
                {"DJANGO_DEBUG": "True", "SENTRY_DSN": None},
            )
            assert settings_module.SOCIALACCOUNT_LOGIN_ON_GET is True
