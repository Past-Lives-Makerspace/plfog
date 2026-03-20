"""Tests for Django system checks in core.checks."""

import os
from unittest.mock import patch

from django.core.checks import Error
from django.test import override_settings

from core.checks import check_webpush_settings

# Build a clean env dict without CI for production-simulation tests
_env_without_ci = {k: v for k, v in os.environ.items() if k != "CI"}


def describe_check_webpush_settings():
    def it_returns_no_errors_when_debug_is_true():
        with override_settings(DEBUG=True):
            errors = check_webpush_settings(app_configs=None)
        assert errors == []

    def it_returns_no_errors_when_ci_is_set():
        with (
            override_settings(DEBUG=False, WEBPUSH_SETTINGS={}),
            patch.dict("os.environ", {"CI": "true"}, clear=False),
        ):
            errors = check_webpush_settings(app_configs=None)
        assert errors == []

    def it_returns_no_errors_when_all_keys_present():
        webpush = {
            "VAPID_PUBLIC_KEY": "test-public-key",
            "VAPID_PRIVATE_KEY": "test-private-key",
            "VAPID_ADMIN_EMAIL": "admin@example.com",
        }
        with (
            override_settings(DEBUG=False, WEBPUSH_SETTINGS=webpush),
            patch.dict("os.environ", _env_without_ci, clear=True),
        ):
            errors = check_webpush_settings(app_configs=None)
        assert errors == []

    def it_returns_errors_for_all_missing_keys():
        webpush = {
            "VAPID_PUBLIC_KEY": "",
            "VAPID_PRIVATE_KEY": "",
            "VAPID_ADMIN_EMAIL": "",
        }
        with (
            override_settings(DEBUG=False, WEBPUSH_SETTINGS=webpush),
            patch.dict("os.environ", _env_without_ci, clear=True),
        ):
            errors = check_webpush_settings(app_configs=None)

        assert len(errors) == 3
        assert all(isinstance(e, Error) for e in errors)
        assert all(e.id == "core.E001" for e in errors)
        assert "VAPID_PUBLIC_KEY" in errors[0].msg
        assert "VAPID_PRIVATE_KEY" in errors[1].msg
        assert "VAPID_ADMIN_EMAIL" in errors[2].msg

    def it_returns_errors_for_partial_keys():
        webpush = {
            "VAPID_PUBLIC_KEY": "present",
            "VAPID_PRIVATE_KEY": "",
            "VAPID_ADMIN_EMAIL": "admin@example.com",
        }
        with (
            override_settings(DEBUG=False, WEBPUSH_SETTINGS=webpush),
            patch.dict("os.environ", _env_without_ci, clear=True),
        ):
            errors = check_webpush_settings(app_configs=None)

        assert len(errors) == 1
        assert errors[0].id == "core.E001"
        assert "VAPID_PRIVATE_KEY" in errors[0].msg

    def it_returns_errors_when_webpush_settings_missing_entirely():
        from django.conf import settings as django_settings

        webpush_backup = django_settings.WEBPUSH_SETTINGS
        del django_settings.WEBPUSH_SETTINGS
        try:
            with (
                override_settings(DEBUG=False),
                patch.dict("os.environ", _env_without_ci, clear=True),
            ):
                errors = check_webpush_settings(app_configs=None)
        finally:
            django_settings.WEBPUSH_SETTINGS = webpush_backup

        assert len(errors) == 3
        assert all(e.id == "core.E001" for e in errors)

    def it_includes_hint_with_env_var_name():
        webpush = {
            "VAPID_PUBLIC_KEY": "",
            "VAPID_PRIVATE_KEY": "present",
            "VAPID_ADMIN_EMAIL": "present",
        }
        with (
            override_settings(DEBUG=False, WEBPUSH_SETTINGS=webpush),
            patch.dict("os.environ", _env_without_ci, clear=True),
        ):
            errors = check_webpush_settings(app_configs=None)

        assert len(errors) == 1
        assert "WEBPUSH_VAPID_PUBLIC_KEY" in errors[0].hint
