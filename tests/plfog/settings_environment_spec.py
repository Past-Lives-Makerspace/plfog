"""BDD specs for the ENVIRONMENT and EMAIL_DELIVERY_ALLOWLIST settings branches."""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured

from tests.plfog.settings_spec import _reload_settings


@pytest.fixture(autouse=True)
def _restore_settings_module(monkeypatch):
    """Leave the settings module as the suite found it: development, no allowlist."""
    yield
    _reload_settings(monkeypatch, {"ENVIRONMENT": None, "EMAIL_DELIVERY_ALLOWLIST": None, "DJANGO_DEBUG": "True"})


def describe_environment():
    def it_defaults_to_development_when_debug_is_on(monkeypatch):
        settings_module = _reload_settings(monkeypatch, {"ENVIRONMENT": None, "DJANGO_DEBUG": "True"})
        assert settings_module.ENVIRONMENT == "development"
        assert settings_module.IS_STAGING is False

    def it_defaults_to_production_when_debug_is_off(monkeypatch):
        settings_module = _reload_settings(monkeypatch, {"ENVIRONMENT": None, "DJANGO_DEBUG": "False"})
        assert settings_module.ENVIRONMENT == "production"
        assert settings_module.IS_STAGING is False

    def it_accepts_staging_ignoring_case_and_whitespace(monkeypatch):
        settings_module = _reload_settings(monkeypatch, {"ENVIRONMENT": " Staging ", "DJANGO_DEBUG": "False"})
        assert settings_module.ENVIRONMENT == "staging"
        assert settings_module.IS_STAGING is True

    def it_refuses_an_unknown_name(monkeypatch):
        with pytest.raises(ImproperlyConfigured, match="ENVIRONMENT must be one of"):
            _reload_settings(monkeypatch, {"ENVIRONMENT": "stagin", "DJANGO_DEBUG": "False"})


def describe_email_delivery_allowlist():
    def it_is_empty_when_unset(monkeypatch):
        settings_module = _reload_settings(monkeypatch, {"EMAIL_DELIVERY_ALLOWLIST": None, "DJANGO_DEBUG": "True"})
        assert settings_module.EMAIL_DELIVERY_ALLOWLIST == frozenset()

    def it_lowercases_strips_and_drops_empty_entries(monkeypatch):
        settings_module = _reload_settings(
            monkeypatch,
            {"EMAIL_DELIVERY_ALLOWLIST": " Josh@Plaza.codes, pastlives.space ,, ", "DJANGO_DEBUG": "True"},
        )
        assert settings_module.EMAIL_DELIVERY_ALLOWLIST == frozenset({"josh@plaza.codes", "pastlives.space"})
