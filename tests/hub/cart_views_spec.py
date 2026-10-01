"""BDD specs for guild cart endpoints."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from django.contrib.auth.models import User
from django.test import Client

from unittest.mock import patch

from billing.exceptions import TabLimitExceededError, TabLockedError
from billing.models import TabEntry
from tests.billing.factories import BillingSettingsFactory, ProductFactory, TabFactory
from tests.features import coming_soon, hide
from tests.membership.factories import GuildFactory, MembershipPlanFactory


def _linked_user(client: Client, *, username: str = "cartu") -> tuple:
    """Create a user + linked Member + Tab (with a saved card) + login."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    member = user.member
    tab = TabFactory(member=member, stripe_payment_method_id="pm_test", stripe_customer_id="cus_test")
    client.login(username=username, password="pass")
    return user, tab


@pytest.mark.django_db
def describe_guild_cart_confirm():
    @pytest.mark.parametrize("switch_off", [hide, coming_soon])
    def it_adds_nothing_while_my_tab_is_off(client: Client, switch_off):
        BillingSettingsFactory()
        guild = GuildFactory()
        product = ProductFactory(guild=guild, price=Decimal("10.00"))
        _user, tab = _linked_user(client, username="cart_off")
        switch_off("my_tab")

        response = client.post(
            f"/guilds/{guild.pk}/cart/confirm/",
            data=json.dumps({"items": [{"product_pk": product.pk, "quantity": 1}]}),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert not TabEntry.objects.filter(tab=tab).exists()

    def it_creates_tab_entries_for_each_cart_item(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        p1 = ProductFactory(guild=guild, name="Laser Time", price=Decimal("10.00"))
        p2 = ProductFactory(guild=guild, name="3D Print", price=Decimal("5.00"))
        _user, tab = _linked_user(client)

        response = client.post(
            f"/guilds/{guild.pk}/cart/confirm/",
            data=json.dumps(
                {
                    "items": [
                        {"product_pk": p1.pk, "quantity": 2},
                        {"product_pk": p2.pk, "quantity": 1},
                    ]
                }
            ),
            content_type="application/json",
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 204
        entries = TabEntry.objects.filter(tab=tab).order_by("description")
        assert entries.count() == 3
        assert entries.filter(description="Laser Time").count() == 2
        assert entries.filter(description="3D Print").count() == 1

    def it_returns_toast_on_success(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        product = ProductFactory(guild=guild, price=Decimal("10.00"))
        _user, _tab = _linked_user(client)

        response = client.post(
            f"/guilds/{guild.pk}/cart/confirm/",
            data=json.dumps({"items": [{"product_pk": product.pk, "quantity": 1}]}),
            content_type="application/json",
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 204
        trigger = json.loads(response["HX-Trigger"])
        assert "showToast" in trigger
        assert trigger["showToast"]["type"] == "success"

    def it_rejects_empty_cart(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        _user, _tab = _linked_user(client)

        response = client.post(
            f"/guilds/{guild.pk}/cart/confirm/",
            data=json.dumps({"items": []}),
            content_type="application/json",
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400

    def it_rejects_invalid_product(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        _user, _tab = _linked_user(client)

        response = client.post(
            f"/guilds/{guild.pk}/cart/confirm/",
            data=json.dumps({"items": [{"product_pk": 99999, "quantity": 1}]}),
            content_type="application/json",
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 400

    def it_requires_login(client: Client):
        guild = GuildFactory()
        response = client.post(f"/guilds/{guild.pk}/cart/confirm/")
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_rejects_get_method(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        _user, _tab = _linked_user(client)

        response = client.get(f"/guilds/{guild.pk}/cart/confirm/")
        assert response.status_code == 405

    def it_rejects_when_no_payment_method(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        MembershipPlanFactory()
        user = User.objects.create_user(username="nocard", password="pass")
        TabFactory(member=user.member, stripe_payment_method_id="")
        client.login(username="nocard", password="pass")

        response = client.post(
            f"/guilds/{guild.pk}/cart/confirm/",
            data=json.dumps({"items": [{"product_pk": 1, "quantity": 1}]}),
            content_type="application/json",
        )
        assert response.status_code == 400

    def it_rejects_invalid_json(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        _user, _tab = _linked_user(client)

        response = client.post(
            f"/guilds/{guild.pk}/cart/confirm/",
            data="not json",
            content_type="application/json",
        )
        assert response.status_code == 400

    def it_returns_error_when_tab_locked_mid_cart(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        product = ProductFactory(guild=guild, price=Decimal("10.00"))
        _user, _tab = _linked_user(client)

        with patch("billing.models.Tab.add_entry", side_effect=TabLockedError("locked")):
            response = client.post(
                f"/guilds/{guild.pk}/cart/confirm/",
                data=json.dumps({"items": [{"product_pk": product.pk, "quantity": 1}]}),
                content_type="application/json",
            )

        assert response.status_code == 400
        trigger = json.loads(response["HX-Trigger"])
        assert trigger["showToast"]["type"] == "error"


@pytest.mark.django_db
def describe_guild_eyop_form():
    @pytest.mark.parametrize("switch_off", [hide, coming_soon])
    def it_adds_nothing_while_my_tab_is_off(client: Client, switch_off):
        BillingSettingsFactory()
        guild = GuildFactory()
        _user, tab = _linked_user(client, username="eyop_off")
        switch_off("my_tab")

        response = client.post(
            f"/guilds/{guild.pk}/eyop-form/",
            {"description": "Custom thing", "amount": "12.50", "quantity": "1"},
        )

        assert response.status_code == 400
        assert not TabEntry.objects.filter(tab=tab).exists()

    def it_returns_form_partial_for_htmx(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        _user, _tab = _linked_user(client, username="eyopu")

        response = client.get(
            f"/guilds/{guild.pk}/eyop-form/",
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert b"description" in response.content.lower() or b"Description" in response.content

    def it_creates_entry_on_post(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        _user, tab = _linked_user(client, username="eyop_post")

        response = client.post(
            f"/guilds/{guild.pk}/eyop-form/",
            {"description": "Custom thing", "amount": "12.50", "quantity": "1"},
        )

        assert response.status_code == 204
        entries = TabEntry.objects.filter(tab=tab)
        assert entries.count() == 1
        entry = entries.first()
        assert entry.description == "Custom thing"
        assert entry.amount == Decimal("12.50")
        # Auto-constructed splits: admin + the fixed guild
        assert entry.splits.count() == 2

    def it_creates_n_entries_when_quantity_is_n(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        _user, tab = _linked_user(client, username="eyop_qty")

        response = client.post(
            f"/guilds/{guild.pk}/eyop-form/",
            {"description": "Bulk", "amount": "3.00", "quantity": "3"},
        )

        assert response.status_code == 204
        assert TabEntry.objects.filter(tab=tab).count() == 3

    def it_requires_login(client: Client):
        guild = GuildFactory()
        response = client.get(f"/guilds/{guild.pk}/eyop-form/")
        assert response.status_code == 302

    def it_returns_error_when_no_payment_method(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        MembershipPlanFactory()
        user = User.objects.create_user(username="nocard_eyop", password="pass")
        TabFactory(member=user.member, stripe_payment_method_id="")
        client.login(username="nocard_eyop", password="pass")

        response = client.post(
            f"/guilds/{guild.pk}/eyop-form/",
            {"description": "Test", "amount": "5.00", "quantity": "1"},
        )
        assert response.status_code == 400

    def it_returns_error_when_tab_locked(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        _user, _tab = _linked_user(client, username="eyop_locked")

        with patch("billing.models.Tab.add_entry", side_effect=TabLockedError("locked")):
            response = client.post(
                f"/guilds/{guild.pk}/eyop-form/",
                {"description": "x", "amount": "1.00", "quantity": "1"},
            )

        assert response.status_code == 400
        trigger = json.loads(response["HX-Trigger"])
        assert trigger["showToast"]["type"] == "error"

    def it_returns_error_when_tab_limit_exceeded(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        _user, _tab = _linked_user(client, username="eyop_over")

        with patch(
            "billing.models.Tab.add_entry",
            side_effect=TabLimitExceededError("over limit"),
        ):
            response = client.post(
                f"/guilds/{guild.pk}/eyop-form/",
                {"description": "x", "amount": "1.00", "quantity": "1"},
            )

        assert response.status_code == 400
        trigger = json.loads(response["HX-Trigger"])
        assert trigger["showToast"]["type"] == "error"

    def it_re_renders_form_on_validation_error(client: Client):
        BillingSettingsFactory()
        guild = GuildFactory()
        _user, _tab = _linked_user(client, username="bad_eyop")

        response = client.post(
            f"/guilds/{guild.pk}/eyop-form/",
            {"description": "", "amount": ""},
        )
        assert response.status_code == 200
