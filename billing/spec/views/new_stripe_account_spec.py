"""BDD specs for "Put new account credentials" on the Stripe tab (#702)."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import Client

from billing.models import BillingSettings

pytestmark = pytest.mark.django_db

SWITCH_URL = "/billing/admin/connect-platform/switch-account/"
SAVE_URL = "/billing/admin/connect-platform/save/"
STRIPE_TAB_URL = "/billing/admin/dashboard/?tab=stripe"

OLD = {
    "connect_platform_publishable_key": "pk_live_old",
    "connect_platform_secret_key": "sk_live_old",
    "connect_platform_webhook_secret": "whsec_live_old",
    "connect_accounts_webhook_secret": "whsec_acct_live_old",
    "test_connect_platform_publishable_key": "pk_test_old",
    "test_connect_platform_secret_key": "sk_test_old",
    "test_connect_platform_webhook_secret": "whsec_test_old",
    "test_connect_accounts_webhook_secret": "whsec_acct_test_old",
}


def _admin(client: Client) -> None:
    User.objects.create_superuser(username="admin", password="pass", email="admin@example.com")
    client.login(username="admin", password="pass")


def _settings(*, test_mode: bool) -> BillingSettings:
    bs = BillingSettings.load()
    bs.test_mode = test_mode
    for name, value in OLD.items():
        setattr(bs, name, value)
    bs.save()
    return bs


def _new(secret_key: str) -> dict[str, str]:
    return {
        "publishable_key": "pk_new",
        "secret_key": secret_key,
        "webhook_secret": "whsec_new",
        "accounts_webhook_secret": "whsec_acct_new",
    }


def _messages(response) -> list[str]:
    return [str(message) for message in get_messages(response.wsgi_request)]


def describe_put_new_account_credentials():
    def it_shows_the_button_and_its_modal_on_the_stripe_tab(client: Client):
        _admin(client)

        html = client.get(STRIPE_TAB_URL).content.decode()

        assert "Put new account credentials" in html
        assert 'id="new-stripe-account-title"' in html
        assert 'action="/billing/admin/connect-platform/switch-account/"' in html

    def it_moves_the_live_keys_to_previous_and_makes_the_new_ones_active(client: Client):
        _admin(client)
        _settings(test_mode=False)

        response = client.post(SWITCH_URL, _new("sk_live_new"))

        bs = BillingSettings.load()
        assert (bs.previous_secret_key, bs.previous_webhook_secret) == ("sk_live_old", "whsec_live_old")
        assert (
            bs.connect_platform_publishable_key,
            bs.connect_platform_secret_key,
            bs.connect_platform_webhook_secret,
            bs.connect_accounts_webhook_secret,
        ) == ("pk_new", "sk_live_new", "whsec_new", "whsec_acct_new")
        assert (bs.test_previous_secret_key, bs.test_connect_platform_secret_key) == ("", "sk_test_old")
        assert _messages(response) == [
            "New Stripe account credentials saved. The old secret key and webhook signing secret moved to "
            "Previous account."
        ]

    def it_moves_the_test_keys_to_previous_and_makes_the_new_ones_active(client: Client):
        _admin(client)
        _settings(test_mode=True)

        client.post(SWITCH_URL, _new("sk_test_new"))

        bs = BillingSettings.load()
        assert (bs.test_previous_secret_key, bs.test_previous_webhook_secret) == ("sk_test_old", "whsec_test_old")
        assert (
            bs.test_connect_platform_publishable_key,
            bs.test_connect_platform_secret_key,
            bs.test_connect_platform_webhook_secret,
            bs.test_connect_accounts_webhook_secret,
        ) == ("pk_new", "sk_test_new", "whsec_new", "whsec_acct_new")
        assert (bs.previous_secret_key, bs.connect_platform_secret_key) == ("", "sk_live_old")

    def it_rejects_a_secret_key_for_the_other_mode_and_moves_nothing(client: Client):
        _admin(client)
        _settings(test_mode=False)

        response = client.post(SWITCH_URL, _new("sk_test_new"))

        bs = BillingSettings.load()
        assert (bs.previous_secret_key, bs.connect_platform_secret_key) == ("", "sk_live_old")
        assert _messages(response) == ["secret_key: A Live mode secret key starts with sk_live_."]

    def it_rejects_the_current_secret_key_and_moves_nothing(client: Client):
        _admin(client)
        _settings(test_mode=False)

        response = client.post(SWITCH_URL, _new("sk_live_old"))

        bs = BillingSettings.load()
        assert (bs.previous_secret_key, bs.connect_platform_webhook_secret) == ("", "whsec_live_old")
        assert _messages(response) == [
            "secret_key: This is the current account's secret key. Paste the new account's key."
        ]

    def it_forbids_a_member_who_is_not_an_admin(client: Client):
        User.objects.create_user(username="member", password="pass", email="member@example.com")
        client.login(username="member", password="pass")
        _settings(test_mode=False)

        response = client.post(SWITCH_URL, _new("sk_live_new"))

        assert response.status_code == 403
        assert BillingSettings.load().connect_platform_secret_key == "sk_live_old"


def describe_the_normal_stripe_settings_save():
    def it_never_moves_credentials_to_previous_account(client: Client):
        _admin(client)
        _settings(test_mode=False)

        client.post(
            SAVE_URL,
            {
                **OLD,
                "connect_platform_secret_key": "sk_live_pasted_over",
                "previous_secret_key": "",
                "previous_webhook_secret": "",
            },
        )

        bs = BillingSettings.load()
        assert bs.connect_platform_secret_key == "sk_live_pasted_over"
        assert (bs.previous_secret_key, bs.previous_webhook_secret) == ("", "")
