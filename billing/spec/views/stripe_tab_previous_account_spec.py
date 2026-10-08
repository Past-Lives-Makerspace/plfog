"""BDD specs for the Stripe tab's previous account section and Connect validation (#702)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.db import connection
from django.test import Client

from billing.forms import ConnectPlatformSettingsForm
from billing.models import BillingSettings

pytestmark = pytest.mark.django_db

SAVE_URL = "/billing/admin/connect-platform/save/"
STRIPE_TAB_URL = "/billing/admin/dashboard/?tab=stripe"

PREVIOUS_FIELDS = (
    "previous_secret_key",
    "previous_webhook_secret",
    "test_previous_secret_key",
    "test_previous_webhook_secret",
)

LIVE_CREDENTIALS = {
    "connect_platform_publishable_key": "pk_live_1",
    "connect_platform_secret_key": "sk_live_1",
    "connect_platform_webhook_secret": "whsec_live_1",
    "connect_accounts_webhook_secret": "whsec_acct_1",
}


def _admin(client: Client) -> None:
    User.objects.create_superuser(username="admin", password="pass", email="admin@example.com")
    client.login(username="admin", password="pass")


def _raw(column: str) -> str:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {column} FROM {BillingSettings._meta.db_table} WHERE id = 1")
        return cursor.fetchone()[0]


def describe_previous_account_section():
    def it_renders_the_four_previous_account_fields_on_the_stripe_tab(client: Client):
        _admin(client)

        html = client.get(STRIPE_TAB_URL).content.decode()

        assert "Previous Account" in html
        for field in PREVIOUS_FIELDS:
            assert f'name="{field}"' in html

    def it_saves_the_previous_keys_encrypted(client: Client):
        _admin(client)

        client.post(
            SAVE_URL,
            {
                "previous_secret_key": "sk_live_old",
                "previous_webhook_secret": "whsec_old",
                "test_previous_secret_key": "sk_test_old",
                "test_previous_webhook_secret": "whsec_test_old",
            },
        )

        bs = BillingSettings.load()
        assert bs.previous_secret_key == "sk_live_old"
        assert bs.previous_webhook_secret == "whsec_old"
        assert bs.test_previous_secret_key == "sk_test_old"
        assert bs.test_previous_webhook_secret == "whsec_test_old"
        assert _raw("previous_secret_key") != "sk_live_old"

    def it_accepts_blank_previous_keys(client: Client):
        _admin(client)
        bs = BillingSettings.load()
        bs.previous_secret_key = "sk_live_old"
        bs.save()

        response = client.post(
            SAVE_URL, {"connect_enabled": "on", **LIVE_CREDENTIALS, **dict.fromkeys(PREVIOUS_FIELDS, "")}
        )

        assert [str(m) for m in get_messages(response.wsgi_request)] == ["Stripe platform settings saved."]
        assert BillingSettings.load().previous_secret_key == ""


def describe_connect_without_a_client_id():
    def it_turns_connect_on_with_no_client_id(client: Client):
        _admin(client)

        client.post(SAVE_URL, {"connect_enabled": "on", **LIVE_CREDENTIALS})

        assert BillingSettings.load().connect_enabled is True

    def it_has_no_client_id_fields_on_the_form_or_the_page(client: Client):
        _admin(client)

        html = client.get(STRIPE_TAB_URL).content.decode()

        assert not [name for name in ConnectPlatformSettingsForm().fields if "client_id" in name]
        assert "client_id" not in html
        assert "Connect Client ID" not in html


def describe_missing_field_errors():
    def it_reports_each_missing_field_once_on_the_form():
        form = ConnectPlatformSettingsForm(instance=BillingSettings.load(), data={"connect_enabled": "on"})

        assert not form.is_valid()
        for field in LIVE_CREDENTIALS:
            assert form.errors[field] == ["Required when Stripe Connect is enabled in the current mode."]

    def it_shows_each_missing_field_message_once_on_the_page(client: Client):
        _admin(client)

        response = client.post(SAVE_URL, {"connect_enabled": "on"})

        messages = [str(m) for m in get_messages(response.wsgi_request)]
        assert len(messages) == len(set(messages)) == len(LIVE_CREDENTIALS)
