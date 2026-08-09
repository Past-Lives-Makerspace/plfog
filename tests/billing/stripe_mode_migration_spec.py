"""Data-migration spec for 0012 — relocating existing Stripe test keys into the test slot.

Uses Django's ``MigrationExecutor`` so we can build a ``BillingSettings`` row at the
schema state *before* the data migration runs, then migrate forward/back and assert the
keys land in the right slot. Each test restores the schema to head in a ``finally`` so
the rest of the suite sees the current DB.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

_APP = "billing"
_BEFORE = "0011_billingsettings_test_connect_client_id_and_more"
_AFTER = "0012_migrate_existing_stripe_keys_to_test_slot"
_HEAD = "0012_migrate_existing_stripe_keys_to_test_slot"


def _migrate(target: str) -> Any:
    """Migrate the billing app to ``target`` and return that state's historical apps."""
    executor = MigrationExecutor(connection)
    executor.migrate([(_APP, target)])
    return executor.loader.project_state([(_APP, target)]).apps


def _make_settings(apps: Any, **kwargs: Any) -> Any:
    BillingSettings = apps.get_model(_APP, "BillingSettings")
    return BillingSettings.objects.create(pk=1, **kwargs)


@pytest.mark.django_db(transaction=True)
def describe_migration_0012_migrate_existing_stripe_keys_to_test_slot():
    def it_relocates_test_keys_into_the_test_slot():
        try:
            apps = _migrate(_BEFORE)
            _make_settings(
                apps,
                connect_client_id="ca_x",
                connect_platform_publishable_key="pk_test_x",
                connect_platform_secret_key="sk_test_x",
                connect_platform_webhook_secret="whsec_x",
                test_mode=False,  # migration should flip this to True
            )

            apps = _migrate(_AFTER)
            BillingSettings = apps.get_model(_APP, "BillingSettings")
            bs = BillingSettings.objects.get(pk=1)

            assert bs.test_mode is True
            assert bs.test_connect_client_id == "ca_x"
            assert bs.test_connect_platform_publishable_key == "pk_test_x"
            assert bs.test_connect_platform_secret_key == "sk_test_x"
            assert bs.test_connect_platform_webhook_secret == "whsec_x"
            assert bs.connect_client_id == ""
            assert bs.connect_platform_publishable_key == ""
            assert bs.connect_platform_secret_key == ""
            assert bs.connect_platform_webhook_secret == ""
        finally:
            _migrate(_HEAD)

    def it_leaves_live_keys_in_place_and_turns_test_mode_off():
        try:
            apps = _migrate(_BEFORE)
            _make_settings(
                apps,
                connect_platform_secret_key="sk_live_x",
                connect_platform_webhook_secret="whsec_live",
                test_mode=True,  # migration should flip this to False
            )

            apps = _migrate(_AFTER)
            BillingSettings = apps.get_model(_APP, "BillingSettings")
            bs = BillingSettings.objects.get(pk=1)

            assert bs.test_mode is False
            assert bs.connect_platform_secret_key == "sk_live_x"
            assert bs.test_connect_platform_secret_key == ""
        finally:
            _migrate(_HEAD)

    def it_defaults_to_test_mode_when_no_secret_is_set():
        try:
            apps = _migrate(_BEFORE)
            _make_settings(apps, test_mode=False)  # no secret at all

            apps = _migrate(_AFTER)
            BillingSettings = apps.get_model(_APP, "BillingSettings")
            bs = BillingSettings.objects.get(pk=1)

            assert bs.test_mode is True
        finally:
            _migrate(_HEAD)

    def it_is_a_no_op_when_no_settings_row_exists():
        try:
            _migrate(_BEFORE)
            apps = _migrate(_AFTER)
            BillingSettings = apps.get_model(_APP, "BillingSettings")
            assert not BillingSettings.objects.filter(pk=1).exists()
        finally:
            _migrate(_HEAD)

    def it_reverse_folds_test_slot_back_into_live_slot():
        try:
            apps = _migrate(_AFTER)
            _make_settings(
                apps,
                test_mode=True,
                test_connect_client_id="ca_t",
                test_connect_platform_publishable_key="pk_test_t",
                test_connect_platform_secret_key="sk_test_t",
                test_connect_platform_webhook_secret="whsec_t",
            )

            apps = _migrate(_BEFORE)
            BillingSettings = apps.get_model(_APP, "BillingSettings")
            bs = BillingSettings.objects.get(pk=1)

            assert bs.connect_client_id == "ca_t"
            assert bs.connect_platform_publishable_key == "pk_test_t"
            assert bs.connect_platform_secret_key == "sk_test_t"
            assert bs.connect_platform_webhook_secret == "whsec_t"
            assert bs.test_connect_client_id == ""
            assert bs.test_connect_platform_secret_key == ""
        finally:
            _migrate(_HEAD)

    def it_reverse_is_a_no_op_when_test_slot_is_empty():
        try:
            apps = _migrate(_AFTER)
            _make_settings(
                apps,
                test_mode=False,
                connect_platform_secret_key="sk_live_z",
            )

            apps = _migrate(_BEFORE)
            BillingSettings = apps.get_model(_APP, "BillingSettings")
            bs = BillingSettings.objects.get(pk=1)

            assert bs.connect_platform_secret_key == "sk_live_z"
            assert bs.test_connect_platform_secret_key == ""
        finally:
            _migrate(_HEAD)
