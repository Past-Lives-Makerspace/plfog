"""BDD specs for staging_import_stripe_test_keys: production's Stripe test keys, re-keyed for staging.

The "production" side is simulated with a throwaway Fernet key: known plaintexts are
encrypted with it and written straight into the row through a cursor, exactly the shape a
refreshed database has, then the command is run with that key in the environment.
"""

from __future__ import annotations

from io import StringIO

import pytest
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from billing.management.commands.staging_import_stripe_test_keys import ENV_KEY
from billing.models import BillingSettings

pytestmark = pytest.mark.django_db

PROD_KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()
SECRET = "sk_test_51ABCDEFimportedfromproduction"
WEBHOOK = "whsec_testwebhooksigningsecret"


@pytest.fixture
def staging(settings, monkeypatch):
    settings.IS_STAGING = True
    monkeypatch.setenv(ENV_KEY, PROD_KEY)
    return settings


def _plant(*, secret: str = SECRET, webhook: str = "") -> None:
    """Write production-keyed ciphertext into the row, past the field's own encryption."""
    row = BillingSettings.load()
    row.test_connect_client_id = "ca_test_prod"
    row.test_connect_platform_publishable_key = "pk_test_prod"
    row.connect_platform_secret_key = "sk_live_should_be_blanked"
    row.connect_platform_webhook_secret = "whsec_live_should_be_blanked"
    row.test_mode = False
    row.save()
    fernet = Fernet(PROD_KEY.encode())
    values = {
        "test_connect_platform_secret_key": fernet.encrypt(secret.encode()).decode() if secret else "",
        "test_connect_platform_webhook_secret": fernet.encrypt(webhook.encode()).decode() if webhook else "",
    }
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE billing_billingsettings "
            "SET test_connect_platform_secret_key = %s, test_connect_platform_webhook_secret = %s WHERE id = 1",
            [values["test_connect_platform_secret_key"], values["test_connect_platform_webhook_secret"]],
        )


def _raw(column: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {column} FROM billing_billingsettings WHERE id = 1")  # noqa: S608
        return cursor.fetchone()[0]


def _run() -> str:
    out = StringIO()
    call_command("staging_import_stripe_test_keys", stdout=out)
    return out.getvalue()


def describe_staging_import_stripe_test_keys():
    def it_refuses_off_staging(settings, monkeypatch):
        settings.IS_STAGING = False
        monkeypatch.setenv(ENV_KEY, PROD_KEY)
        _plant()
        with pytest.raises(CommandError, match="ENVIRONMENT=staging"):
            _run()

    def it_refuses_without_the_production_key(settings, monkeypatch):
        settings.IS_STAGING = True
        monkeypatch.delenv(ENV_KEY, raising=False)
        _plant()
        with pytest.raises(CommandError, match=ENV_KEY):
            _run()

    def it_refuses_a_malformed_production_key(staging, monkeypatch):
        monkeypatch.setenv(ENV_KEY, "not-a-fernet-key")
        _plant()
        with pytest.raises(CommandError, match="malformed"):
            _run()

    def it_refuses_when_there_is_no_row(staging):
        BillingSettings.objects.all().delete()
        with pytest.raises(CommandError, match="No BillingSettings row"):
            _run()

    def describe_with_the_wrong_key():
        def it_names_the_field_and_writes_nothing(staging, monkeypatch):
            _plant(webhook=WEBHOOK)
            planted_secret = _raw("test_connect_platform_secret_key")
            monkeypatch.setenv(ENV_KEY, OTHER_KEY)
            with pytest.raises(CommandError, match="test_connect_platform_secret_key"):
                _run()
            assert _raw("test_connect_platform_secret_key") == planted_secret
            row = BillingSettings.load()
            assert row.test_mode is False
            assert row.connect_platform_secret_key == "sk_live_should_be_blanked"

    def describe_happy_path():
        def it_rekeys_the_test_secrets_blanks_the_live_ones_and_forces_test_mode(staging):
            _plant(webhook=WEBHOOK)
            assert BillingSettings.load().test_connect_platform_secret_key == ""  # unreadable under the staging key
            _run()
            row = BillingSettings.load()
            assert row.test_connect_platform_secret_key == SECRET
            assert row.test_connect_platform_webhook_secret == WEBHOOK
            assert row.test_connect_client_id == "ca_test_prod"
            assert row.test_connect_platform_publishable_key == "pk_test_prod"
            assert row.connect_platform_secret_key == ""
            assert row.connect_platform_webhook_secret == ""
            assert row.test_mode is True
            assert _raw("test_connect_platform_secret_key") != SECRET  # stored encrypted, not in the clear

        def it_prints_masked_values_and_never_the_plaintext(staging):
            _plant(webhook=WEBHOOK)
            output = _run()
            assert f"test_connect_platform_secret_key: imported sk_test_... ({len(SECRET)} characters)" in output
            assert f"test_connect_platform_webhook_secret: imported whsec_te... ({len(WEBHOOK)} characters)" in output
            assert SECRET not in output
            assert WEBHOOK not in output

        def it_reports_a_blank_production_value_and_keeps_it_blank(staging):
            _plant(webhook="")
            output = _run()
            assert "test_connect_platform_webhook_secret: blank on production" in output
            assert BillingSettings.load().test_connect_platform_webhook_secret == ""
            assert BillingSettings.load().test_connect_platform_secret_key == SECRET
