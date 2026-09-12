"""Tests for Django system checks in core.checks."""

import os
from unittest.mock import patch

import pytest
from django.core.checks import Error, registry
from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.test import override_settings

from core.checks import check_public_hosts_are_allowed, check_webpush_settings

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


def describe_check_public_hosts_are_allowed():
    def it_returns_no_errors_when_debug_is_true():
        with override_settings(DEBUG=True, PUBLIC_HOSTS=["book.example.com"], ALLOWED_HOSTS=[]):
            errors = check_public_hosts_are_allowed(app_configs=None)
        assert errors == []

    def it_returns_no_errors_when_ci_is_set():
        with (
            override_settings(DEBUG=False, PUBLIC_HOSTS=["book.example.com"], ALLOWED_HOSTS=[]),
            patch.dict("os.environ", {"CI": "true"}, clear=False),
        ):
            errors = check_public_hosts_are_allowed(app_configs=None)
        assert errors == []

    def it_returns_no_errors_when_every_public_host_is_allowed():
        with (
            override_settings(
                DEBUG=False,
                PUBLIC_HOSTS=["book.example.com", "classes.example.org"],
                ALLOWED_HOSTS=["members.example.com", "book.example.com", "classes.example.org"],
            ),
            patch.dict("os.environ", _env_without_ci, clear=True),
        ):
            errors = check_public_hosts_are_allowed(app_configs=None)
        assert errors == []

    def it_accepts_a_subdomain_wildcard_in_allowed_hosts():
        with (
            override_settings(DEBUG=False, PUBLIC_HOSTS=["book.example.com"], ALLOWED_HOSTS=[".example.com"]),
            patch.dict("os.environ", _env_without_ci, clear=True),
        ):
            errors = check_public_hosts_are_allowed(app_configs=None)
        assert errors == []

    def it_ignores_a_port_on_the_public_host():
        with (
            override_settings(
                DEBUG=False, PUBLIC_HOSTS=["book.example.test:8000"], ALLOWED_HOSTS=["book.example.test"]
            ),
            patch.dict("os.environ", _env_without_ci, clear=True),
        ):
            errors = check_public_hosts_are_allowed(app_configs=None)
        assert errors == []

    def it_returns_one_error_per_public_host_missing_from_allowed_hosts():
        with (
            override_settings(
                DEBUG=False,
                PUBLIC_HOSTS=["book.example.com", "classes.example.org"],
                ALLOWED_HOSTS=["members.example.com"],
            ),
            patch.dict("os.environ", _env_without_ci, clear=True),
        ):
            errors = check_public_hosts_are_allowed(app_configs=None)

        assert len(errors) == 2
        assert all(isinstance(e, Error) for e in errors)
        assert all(e.id == "core.E002" for e in errors)
        assert "book.example.com" in errors[0].msg
        assert "classes.example.org" in errors[1].msg
        assert "DJANGO_ALLOWED_HOSTS" in errors[0].hint

    def it_names_only_the_missing_host():
        with (
            override_settings(
                DEBUG=False,
                PUBLIC_HOSTS=["book.example.com", "classes.example.org"],
                ALLOWED_HOSTS=["book.example.com"],
            ),
            patch.dict("os.environ", _env_without_ci, clear=True),
        ):
            errors = check_public_hosts_are_allowed(app_configs=None)

        assert len(errors) == 1
        assert "classes.example.org" in errors[0].msg
        assert "book.example.com" not in errors[0].msg

    def it_is_registered_as_a_plain_check_not_a_deploy_check():
        # The whole point: Render runs migrate and the seeders at container start, never
        # ``check --deploy``, so a deploy-tagged check would never fire in production.
        assert check_public_hosts_are_allowed in registry.registry.registered_checks
        assert check_public_hosts_are_allowed not in registry.registry.deployment_checks

    def it_fails_a_plain_manage_py_check_on_a_mismatch():
        with (
            override_settings(DEBUG=False, PUBLIC_HOSTS=["classes.example.org"], ALLOWED_HOSTS=["members.example.com"]),
            patch.dict("os.environ", _env_without_ci, clear=True),
            pytest.raises(SystemCheckError, match="core.E002"),
        ):
            call_command("check")
