"""Tests for membership views."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.urls import reverse
from django.utils import timezone

from membership.models import Buyable, Order
from tests.core.factories import UserFactory
from tests.membership.factories import (
    BuyableFactory,
    GuildFactory,
    GuildWishlistItemFactory,
    LeaseFactory,
    MemberFactory,
    OrderFactory,
    SpaceFactory,
)

pytestmark = pytest.mark.django_db


def _make_lead(guild, user):
    """Set user as the guild lead via Guild.guild_lead FK."""
    member = MemberFactory(user=user)
    guild.guild_lead = member
    guild.save()
    return member


# ---------------------------------------------------------------------------
# guild_list
# ---------------------------------------------------------------------------


def describe_guild_list():
    def it_returns_200_for_anonymous(client):
        GuildFactory(name="Ceramics Guild")
        url = reverse("guild_list")
        resp = client.get(url)
        assert resp.status_code == 200

    def it_uses_guild_list_template(client):
        url = reverse("guild_list")
        resp = client.get(url)
        assert "membership/guild_list.html" in [t.name for t in resp.templates]

    def it_lists_only_active_guilds(client):
        active = GuildFactory(name="Active Guild", is_active=True)
        GuildFactory(name="Inactive Guild", is_active=False)
        url = reverse("guild_list")
        resp = client.get(url)
        guilds = list(resp.context["guilds"])
        assert active in guilds
        assert not any(g.name == "Inactive Guild" for g in guilds)

    def it_returns_empty_list_when_no_active_guilds(client):
        GuildFactory(name="Inactive Only", is_active=False)
        url = reverse("guild_list")
        resp = client.get(url)
        assert list(resp.context["guilds"]) == []


# ---------------------------------------------------------------------------
# guild_detail
# ---------------------------------------------------------------------------


def describe_guild_detail():
    def it_returns_200_for_anonymous(client):
        guild = GuildFactory(name="Detail Guild", is_active=True)
        url = reverse("guild_detail", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 200

    def it_uses_guild_detail_template(client):
        guild = GuildFactory(name="Template Guild", is_active=True)
        url = reverse("guild_detail", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert "membership/guild_detail.html" in [t.name for t in resp.templates]

    def it_returns_404_for_missing_slug(client):
        url = reverse("guild_detail", kwargs={"slug": "does-not-exist"})
        resp = client.get(url)
        assert resp.status_code == 404

    def it_returns_404_for_inactive_guild(client):
        guild = GuildFactory(name="Hidden Guild", is_active=False)
        url = reverse("guild_detail", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 404

    def it_includes_active_buyables_in_context(client):
        guild = GuildFactory(name="Buyable Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Guild Tote Bag", is_active=True)
        BuyableFactory(guild=guild, name="Inactive Item", is_active=False)
        url = reverse("guild_detail", kwargs={"slug": guild.slug})
        resp = client.get(url)
        buyables = list(resp.context["buyables"])
        assert buyable in buyables
        assert not any(b.name == "Inactive Item" for b in buyables)

    def it_includes_unfulfilled_wishlist_items(client):
        guild = GuildFactory(name="Wishlist Guild", is_active=True)
        item = GuildWishlistItemFactory(guild=guild, name="Wheel Needed")
        url = reverse("guild_detail", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert item in list(resp.context["wishlist_items"])

    def it_includes_spaces_from_active_leases(client):
        guild = GuildFactory(name="Spaces Guild", is_active=True)
        space = SpaceFactory(space_id="S-900", name="Studio 9")
        today = timezone.now().date()
        LeaseFactory(
            tenant_obj=guild,
            space=space,
            start_date=today - timedelta(days=10),
        )
        url = reverse("guild_detail", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert space in resp.context["spaces"]


def describe_guild_detail_anonymous_context():
    def it_sets_is_lead_false_for_anonymous(client):
        guild = GuildFactory(name="Non-Lead Guild", is_active=True)
        url = reverse("guild_detail", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.context["is_lead"] is False


def describe_guild_detail_authenticated_context():
    def it_sets_is_lead_true_when_user_is_guild_lead(client):
        guild = GuildFactory(name="Lead Guild", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("guild_detail", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.context["is_lead"] is True

    def it_sets_is_lead_false_for_non_lead_user(client):
        guild = GuildFactory(name="Non-Lead Auth Guild", is_active=True)
        user = UserFactory()
        client.force_login(user)
        url = reverse("guild_detail", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.context["is_lead"] is False

    def it_sets_is_lead_true_for_staff_user(client):
        guild = GuildFactory(name="Staff Lead Guild", is_active=True)
        user = UserFactory()
        user.is_staff = True
        user.save()
        client.force_login(user)
        url = reverse("guild_detail", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.context["is_lead"] is True


# ---------------------------------------------------------------------------
# buyable_detail
# ---------------------------------------------------------------------------


def describe_buyable_detail():
    def it_returns_200_for_anonymous(client):
        guild = GuildFactory(name="Shop Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Membership Token", is_active=True)
        url = reverse("buyable_detail", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert resp.status_code == 200

    def it_uses_buyable_detail_template(client):
        guild = GuildFactory(name="Template Shop Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Workshop Pass", is_active=True)
        url = reverse("buyable_detail", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert "membership/buyable_detail.html" in [t.name for t in resp.templates]

    def it_returns_404_for_missing_guild(client):
        url = reverse("buyable_detail", kwargs={"slug": "no-guild", "buyable_slug": "no-item"})
        resp = client.get(url)
        assert resp.status_code == 404

    def it_returns_404_for_inactive_guild(client):
        guild = GuildFactory(name="Inactive Shop Guild", is_active=False)
        buyable = BuyableFactory(guild=guild, name="Orphan Buyable", is_active=True)
        url = reverse("buyable_detail", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert resp.status_code == 404

    def it_returns_404_for_inactive_buyable(client):
        guild = GuildFactory(name="Active Shop Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Inactive Buyable", is_active=False)
        url = reverse("buyable_detail", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert resp.status_code == 404

    def it_includes_guild_and_buyable_in_context(client):
        guild = GuildFactory(name="Context Shop Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Context Item", is_active=True)
        url = reverse("buyable_detail", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert resp.context["guild"] == guild
        assert resp.context["buyable"] == buyable


# ---------------------------------------------------------------------------
# buyable_checkout
# ---------------------------------------------------------------------------


def describe_buyable_checkout():
    def it_redirects_to_buyable_detail_on_get(client):
        guild = GuildFactory(name="Checkout Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Checkout Item", is_active=True)
        url = reverse("buyable_checkout", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert resp.status_code == 302
        assert reverse("buyable_detail", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug}) in resp["Location"]

    def it_creates_order_and_redirects_to_stripe_on_post(client):
        guild = GuildFactory(name="Stripe Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Stripe Item", unit_price=Decimal("25.00"), is_active=True)
        url = reverse("buyable_checkout", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})

        mock_session = MagicMock()
        mock_session.id = "sess_test"
        mock_session.url = "https://stripe.com/checkout/sess_test"

        with patch("membership.views.create_checkout_session", return_value=mock_session):
            resp = client.post(url, {"quantity": "1"})

        assert resp.status_code == 302
        assert resp["Location"] == "https://stripe.com/checkout/sess_test"

    def it_creates_a_pending_order_on_post(client):
        guild = GuildFactory(name="Order Create Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Order Create Item", unit_price=Decimal("10.00"), is_active=True)
        url = reverse("buyable_checkout", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})

        mock_session = MagicMock()
        mock_session.id = "sess_order_test"
        mock_session.url = "https://stripe.com/pay"

        with patch("membership.views.create_checkout_session", return_value=mock_session):
            client.post(url, {"quantity": "1"})

        assert Order.objects.filter(
            buyable=buyable,
            stripe_checkout_session_id="sess_order_test",
            status=Order.Status.PENDING,
        ).exists()

    def it_associates_order_with_authenticated_user(client):
        guild = GuildFactory(name="Auth Order Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Auth Order Item", unit_price=Decimal("20.00"), is_active=True)
        user = UserFactory()
        client.force_login(user)
        url = reverse("buyable_checkout", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})

        mock_session = MagicMock()
        mock_session.id = "sess_auth"
        mock_session.url = "https://stripe.com/pay/auth"

        with patch("membership.views.create_checkout_session", return_value=mock_session):
            client.post(url, {"quantity": "1"})

        order = Order.objects.get(stripe_checkout_session_id="sess_auth")
        assert order.user == user

    def it_clamps_quantity_below_one_to_one(client):
        guild = GuildFactory(name="Clamp Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Clamp Item", unit_price=Decimal("10.00"), is_active=True)
        url = reverse("buyable_checkout", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})

        mock_session = MagicMock()
        mock_session.id = "sess_clamp"
        mock_session.url = "https://stripe.com/pay/clamp"

        with patch("membership.views.create_checkout_session", return_value=mock_session):
            client.post(url, {"quantity": "0"})

        order = Order.objects.get(stripe_checkout_session_id="sess_clamp")
        assert order.quantity == 1

    def it_returns_404_for_missing_guild_on_post(client):
        url = reverse("buyable_checkout", kwargs={"slug": "no-guild", "buyable_slug": "no-item"})
        resp = client.post(url, {"quantity": "1"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# buyable_qr
# ---------------------------------------------------------------------------


def describe_buyable_qr():
    def it_returns_200_with_svg_content_type(client):
        guild = GuildFactory(name="QR Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="QR Item", is_active=True)
        url = reverse("buyable_qr", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp["Content-Type"] == "image/svg+xml"

    def it_returns_svg_content(client):
        guild = GuildFactory(name="QR SVG Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="QR SVG Item", is_active=True)
        url = reverse("buyable_qr", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert b"<svg" in resp.content

    def it_returns_404_for_inactive_guild(client):
        guild = GuildFactory(name="QR Inactive Guild", is_active=False)
        buyable = BuyableFactory(guild=guild, name="QR Inactive Item", is_active=True)
        url = reverse("buyable_qr", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert resp.status_code == 404

    def it_returns_404_for_inactive_buyable(client):
        guild = GuildFactory(name="QR Active Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="QR Inactive Buyable", is_active=False)
        url = reverse("buyable_qr", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# checkout_success
# ---------------------------------------------------------------------------


def describe_checkout_success():
    def it_returns_200_with_no_session_id(client):
        url = reverse("checkout_success")
        resp = client.get(url)
        assert resp.status_code == 200

    def it_uses_checkout_success_template(client):
        url = reverse("checkout_success")
        resp = client.get(url)
        assert "membership/checkout_success.html" in [t.name for t in resp.templates]

    def it_provides_order_as_none_with_no_session_id(client):
        url = reverse("checkout_success")
        resp = client.get(url)
        assert resp.context["order"] is None

    @patch("stripe.checkout.Session.retrieve")
    @patch("membership.stripe_utils.get_stripe_key", return_value="sk_test")
    def it_marks_order_paid_with_valid_session_id(mock_key, mock_retrieve, client):
        guild = GuildFactory(name="Success Pay Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Success Pay Item", is_active=True)
        order = OrderFactory(
            buyable=buyable,
            stripe_checkout_session_id="cs_success_test",
            status=Order.Status.PENDING,
        )

        mock_session = MagicMock()
        mock_session.customer_details.email = "buyer@example.com"
        mock_retrieve.return_value = mock_session

        url = reverse("checkout_success") + "?session_id=cs_success_test"
        resp = client.get(url)

        assert resp.status_code == 200
        order.refresh_from_db()
        assert order.status == Order.Status.PAID
        assert order.email == "buyer@example.com"
        assert order.paid_at is not None
        assert resp.context["order"] == order

    @patch("stripe.checkout.Session.retrieve")
    @patch("membership.stripe_utils.get_stripe_key", return_value="sk_test")
    def it_skips_update_if_order_already_paid(mock_key, mock_retrieve, client):
        guild = GuildFactory(name="Already Paid Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Already Paid Item", is_active=True)
        order = OrderFactory(
            buyable=buyable,
            stripe_checkout_session_id="cs_already_paid",
            status=Order.Status.PAID,
        )

        mock_session = MagicMock()
        mock_session.customer_details.email = "already@example.com"
        mock_retrieve.return_value = mock_session

        url = reverse("checkout_success") + "?session_id=cs_already_paid"
        resp = client.get(url)

        assert resp.status_code == 200
        order.refresh_from_db()
        assert order.status == Order.Status.PAID
        assert resp.context["order"] == order

    @patch("stripe.checkout.Session.retrieve")
    @patch("membership.stripe_utils.get_stripe_key", return_value="sk_test")
    def it_handles_missing_customer_details(mock_key, mock_retrieve, client):
        guild = GuildFactory(name="No Email Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="No Email Item", is_active=True)
        OrderFactory(
            buyable=buyable,
            stripe_checkout_session_id="cs_no_email",
            status=Order.Status.PENDING,
        )

        mock_session = MagicMock()
        mock_session.customer_details = None
        mock_retrieve.return_value = mock_session

        url = reverse("checkout_success") + "?session_id=cs_no_email"
        resp = client.get(url)

        assert resp.status_code == 200
        order = Order.objects.get(stripe_checkout_session_id="cs_no_email")
        assert order.status == Order.Status.PAID
        assert order.email == ""

    @patch("stripe.checkout.Session.retrieve")
    @patch("membership.stripe_utils.get_stripe_key", return_value="sk_test")
    def it_handles_stripe_error_gracefully(mock_key, mock_retrieve, client):
        import stripe

        mock_retrieve.side_effect = stripe.StripeError("Connection error")

        url = reverse("checkout_success") + "?session_id=cs_error_test"
        resp = client.get(url)

        assert resp.status_code == 200
        assert resp.context["order"] is None


# ---------------------------------------------------------------------------
# checkout_cancel
# ---------------------------------------------------------------------------


def describe_checkout_cancel():
    def it_returns_200(client):
        url = reverse("checkout_cancel")
        resp = client.get(url)
        assert resp.status_code == 200

    def it_uses_checkout_cancel_template(client):
        url = reverse("checkout_cancel")
        resp = client.get(url)
        assert "membership/checkout_cancel.html" in [t.name for t in resp.templates]


# ---------------------------------------------------------------------------
# user_orders
# ---------------------------------------------------------------------------


def describe_user_orders():
    def it_redirects_anonymous_to_login(client):
        url = reverse("user_orders")
        resp = client.get(url)
        assert resp.status_code == 302
        assert "/accounts/" in resp["Location"] or "login" in resp["Location"]

    def it_returns_200_for_authenticated_user(client):
        user = UserFactory()
        client.force_login(user)
        url = reverse("user_orders")
        resp = client.get(url)
        assert resp.status_code == 200

    def it_uses_user_orders_template(client):
        user = UserFactory()
        client.force_login(user)
        url = reverse("user_orders")
        resp = client.get(url)
        assert "membership/user_orders.html" in [t.name for t in resp.templates]

    def it_shows_only_the_users_orders(client):
        guild = GuildFactory(name="User Orders Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="User Orders Item", is_active=True)
        user = UserFactory()
        other_user = UserFactory()
        own_order = OrderFactory(buyable=buyable, user=user)
        OrderFactory(buyable=buyable, user=other_user)
        client.force_login(user)
        url = reverse("user_orders")
        resp = client.get(url)
        orders = list(resp.context["orders"])
        assert own_order in orders
        assert len(orders) == 1


# ---------------------------------------------------------------------------
# guild_manage
# ---------------------------------------------------------------------------


def describe_guild_manage():
    def it_redirects_anonymous_to_login(client):
        guild = GuildFactory(name="Manage Guild", is_active=True)
        url = reverse("guild_manage", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 302

    def it_returns_403_for_non_lead_authenticated_user(client):
        guild = GuildFactory(name="Manage Non-Lead Guild", is_active=True)
        user = UserFactory()
        client.force_login(user)
        url = reverse("guild_manage", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 403

    def it_returns_200_for_guild_lead(client):
        guild = GuildFactory(name="Manage Lead Guild", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("guild_manage", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 200

    def it_returns_200_for_staff_user(client):
        guild = GuildFactory(name="Manage Staff Guild", is_active=True)
        user = UserFactory()
        user.is_staff = True
        user.save()
        client.force_login(user)
        url = reverse("guild_manage", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 200

    def it_uses_guild_manage_template(client):
        guild = GuildFactory(name="Manage Template Guild", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("guild_manage", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert "membership/guild_manage.html" in [t.name for t in resp.templates]

    def it_includes_buyables_in_context(client):
        guild = GuildFactory(name="Manage Buyables Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Manage Buyable", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("guild_manage", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert buyable in list(resp.context["buyables"])


# ---------------------------------------------------------------------------
# buyable_add
# ---------------------------------------------------------------------------


def describe_buyable_add():
    def it_redirects_anonymous_to_login(client):
        guild = GuildFactory(name="Add Anon Guild", is_active=True)
        url = reverse("buyable_add", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 302

    def it_returns_403_for_non_lead_authenticated_user(client):
        guild = GuildFactory(name="Add Non-Lead Guild", is_active=True)
        user = UserFactory()
        client.force_login(user)
        url = reverse("buyable_add", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 403

    def it_returns_200_on_get_for_guild_lead(client):
        guild = GuildFactory(name="Add Lead Guild", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("buyable_add", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 200

    def it_uses_buyable_form_template(client):
        guild = GuildFactory(name="Add Form Guild", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("buyable_add", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert "membership/buyable_form.html" in [t.name for t in resp.templates]

    def it_creates_buyable_on_valid_post(client):
        guild = GuildFactory(name="Add Post Guild", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("buyable_add", kwargs={"slug": guild.slug})
        resp = client.post(
            url,
            {
                "name": "New Membership Card",
                "description": "A card for new members.",
                "unit_price": "15.00",
                "is_active": "on",
            },
        )
        assert resp.status_code == 302
        assert Buyable.objects.filter(guild=guild, name="New Membership Card").exists()

    def it_redirects_to_guild_manage_after_valid_post(client):
        guild = GuildFactory(name="Add Redirect Guild", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("buyable_add", kwargs={"slug": guild.slug})
        resp = client.post(
            url,
            {
                "name": "Redirect Buyable",
                "description": "",
                "unit_price": "5.00",
                "is_active": "on",
            },
        )
        assert resp.status_code == 302
        assert reverse("guild_manage", kwargs={"slug": guild.slug}) in resp["Location"]

    def it_returns_200_with_errors_on_invalid_post(client):
        guild = GuildFactory(name="Add Invalid Post Guild", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("buyable_add", kwargs={"slug": guild.slug})
        resp = client.post(url, {"name": "", "unit_price": ""})
        assert resp.status_code == 200

    def it_returns_200_for_staff_user(client):
        guild = GuildFactory(name="Add Staff Guild", is_active=True)
        user = UserFactory()
        user.is_staff = True
        user.save()
        client.force_login(user)
        url = reverse("buyable_add", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# buyable_edit
# ---------------------------------------------------------------------------


def describe_buyable_edit():
    def it_redirects_anonymous_to_login(client):
        guild = GuildFactory(name="Edit Anon Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Edit Anon Item", is_active=True)
        url = reverse("buyable_edit", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert resp.status_code == 302

    def it_returns_403_for_non_lead_authenticated_user(client):
        guild = GuildFactory(name="Edit Non-Lead Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Edit Non-Lead Item", is_active=True)
        user = UserFactory()
        client.force_login(user)
        url = reverse("buyable_edit", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert resp.status_code == 403

    def it_returns_200_on_get_for_guild_lead(client):
        guild = GuildFactory(name="Edit Lead Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Edit Lead Item", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("buyable_edit", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert resp.status_code == 200

    def it_uses_buyable_form_template(client):
        guild = GuildFactory(name="Edit Template Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Edit Template Item", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("buyable_edit", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert "membership/buyable_form.html" in [t.name for t in resp.templates]

    def it_updates_buyable_on_valid_post(client):
        guild = GuildFactory(name="Edit Post Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Old Name", unit_price=Decimal("10.00"), is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("buyable_edit", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.post(
            url,
            {
                "name": "Updated Name",
                "description": "",
                "unit_price": "20.00",
                "is_active": "on",
            },
        )
        assert resp.status_code == 302
        buyable.refresh_from_db()
        assert buyable.name == "Updated Name"

    def it_redirects_to_guild_manage_after_valid_post(client):
        guild = GuildFactory(name="Edit Redirect Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Edit Redirect Item", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("buyable_edit", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.post(
            url,
            {
                "name": "Edit Redirect Updated",
                "description": "",
                "unit_price": "30.00",
                "is_active": "on",
            },
        )
        assert resp.status_code == 302
        assert reverse("guild_manage", kwargs={"slug": guild.slug}) in resp["Location"]

    def it_returns_200_for_staff_user(client):
        guild = GuildFactory(name="Edit Staff Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Edit Staff Item", is_active=True)
        user = UserFactory()
        user.is_staff = True
        user.save()
        client.force_login(user)
        url = reverse("buyable_edit", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert resp.status_code == 200

    def it_includes_buyable_instance_in_context(client):
        guild = GuildFactory(name="Edit Context Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Edit Context Item", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("buyable_edit", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.get(url)
        assert resp.context["buyable"] == buyable

    def it_returns_200_with_errors_on_invalid_post(client):
        guild = GuildFactory(name="Edit Invalid Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Edit Invalid Item", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("buyable_edit", kwargs={"slug": guild.slug, "buyable_slug": buyable.slug})
        resp = client.post(url, {"name": "", "unit_price": ""})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# guild_orders
# ---------------------------------------------------------------------------


def describe_guild_orders():
    def it_redirects_anonymous_to_login(client):
        guild = GuildFactory(name="Orders Anon Guild", is_active=True)
        url = reverse("guild_orders", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 302

    def it_returns_403_for_non_lead_authenticated_user(client):
        guild = GuildFactory(name="Orders Non-Lead Guild", is_active=True)
        user = UserFactory()
        client.force_login(user)
        url = reverse("guild_orders", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 403

    def it_returns_200_for_guild_lead(client):
        guild = GuildFactory(name="Orders Lead Guild", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("guild_orders", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 200

    def it_returns_200_for_staff_user(client):
        guild = GuildFactory(name="Orders Staff Guild", is_active=True)
        user = UserFactory()
        user.is_staff = True
        user.save()
        client.force_login(user)
        url = reverse("guild_orders", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert resp.status_code == 200

    def it_uses_guild_orders_template(client):
        guild = GuildFactory(name="Orders Template Guild", is_active=True)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("guild_orders", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert "membership/guild_orders.html" in [t.name for t in resp.templates]

    def it_shows_orders_for_the_guild(client):
        guild = GuildFactory(name="Orders Context Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="Orders Context Item", is_active=True)
        order = OrderFactory(buyable=buyable)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("guild_orders", kwargs={"slug": guild.slug})
        resp = client.get(url)
        assert order in list(resp.context["orders"])


# ---------------------------------------------------------------------------
# order_detail
# ---------------------------------------------------------------------------


def describe_order_detail():
    def it_redirects_anonymous_to_login(client):
        guild = GuildFactory(name="OD Anon Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="OD Anon Item", is_active=True)
        order = OrderFactory(buyable=buyable)
        url = reverse("order_detail", kwargs={"slug": guild.slug, "pk": order.pk})
        resp = client.get(url)
        assert resp.status_code == 302

    def it_returns_403_for_non_lead_authenticated_user(client):
        guild = GuildFactory(name="OD Non-Lead Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="OD Non-Lead Item", is_active=True)
        order = OrderFactory(buyable=buyable)
        user = UserFactory()
        client.force_login(user)
        url = reverse("order_detail", kwargs={"slug": guild.slug, "pk": order.pk})
        resp = client.get(url)
        assert resp.status_code == 403

    def it_returns_200_for_guild_lead(client):
        guild = GuildFactory(name="OD Lead Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="OD Lead Item", is_active=True)
        order = OrderFactory(buyable=buyable)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("order_detail", kwargs={"slug": guild.slug, "pk": order.pk})
        resp = client.get(url)
        assert resp.status_code == 200

    def it_returns_200_for_staff_user(client):
        guild = GuildFactory(name="OD Staff Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="OD Staff Item", is_active=True)
        order = OrderFactory(buyable=buyable)
        user = UserFactory()
        user.is_staff = True
        user.save()
        client.force_login(user)
        url = reverse("order_detail", kwargs={"slug": guild.slug, "pk": order.pk})
        resp = client.get(url)
        assert resp.status_code == 200

    def it_uses_order_detail_template(client):
        guild = GuildFactory(name="OD Template Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="OD Template Item", is_active=True)
        order = OrderFactory(buyable=buyable)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("order_detail", kwargs={"slug": guild.slug, "pk": order.pk})
        resp = client.get(url)
        assert "membership/order_detail.html" in [t.name for t in resp.templates]

    def it_includes_order_and_form_in_context(client):
        guild = GuildFactory(name="OD Context Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="OD Context Item", is_active=True)
        order = OrderFactory(buyable=buyable)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("order_detail", kwargs={"slug": guild.slug, "pk": order.pk})
        resp = client.get(url)
        assert resp.context["order"] == order
        assert "form" in resp.context

    def it_returns_404_for_order_belonging_to_different_guild(client):
        guild_a = GuildFactory(name="OD Guild A", is_active=True)
        guild_b = GuildFactory(name="OD Guild B", is_active=True)
        buyable_b = BuyableFactory(guild=guild_b, name="Guild B Item", is_active=True)
        order = OrderFactory(buyable=buyable_b)
        user = UserFactory()
        _make_lead(guild_a, user)
        client.force_login(user)
        url = reverse("order_detail", kwargs={"slug": guild_a.slug, "pk": order.pk})
        resp = client.get(url)
        assert resp.status_code == 404


def describe_order_detail_post_actions():
    def it_fulfills_order_on_post_with_action_fulfill(client):
        guild = GuildFactory(name="OD Fulfill Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="OD Fulfill Item", is_active=True)
        order = OrderFactory(buyable=buyable)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("order_detail", kwargs={"slug": guild.slug, "pk": order.pk})
        resp = client.post(url, {"action": "fulfill"})
        assert resp.status_code == 302
        order.refresh_from_db()
        assert order.is_fulfilled is True
        assert order.fulfilled_by == user
        assert order.fulfilled_at is not None

    def it_updates_notes_on_post_with_action_notes(client):
        guild = GuildFactory(name="OD Notes Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="OD Notes Item", is_active=True)
        order = OrderFactory(buyable=buyable)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("order_detail", kwargs={"slug": guild.slug, "pk": order.pk})
        resp = client.post(url, {"action": "notes", "notes": "Ready for pickup"})
        assert resp.status_code == 302
        order.refresh_from_db()
        assert order.notes == "Ready for pickup"

    def it_redirects_back_to_order_detail_after_post(client):
        guild = GuildFactory(name="OD Redirect Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="OD Redirect Item", is_active=True)
        order = OrderFactory(buyable=buyable)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("order_detail", kwargs={"slug": guild.slug, "pk": order.pk})
        resp = client.post(url, {"action": "fulfill"})
        assert resp.status_code == 302
        expected = reverse("order_detail", kwargs={"slug": guild.slug, "pk": order.pk})
        assert expected in resp["Location"]

    def it_redirects_on_unknown_action(client):
        guild = GuildFactory(name="OD Unknown Guild", is_active=True)
        buyable = BuyableFactory(guild=guild, name="OD Unknown Item", is_active=True)
        order = OrderFactory(buyable=buyable)
        user = UserFactory()
        _make_lead(guild, user)
        client.force_login(user)
        url = reverse("order_detail", kwargs={"slug": guild.slug, "pk": order.pk})
        resp = client.post(url, {"action": "unknown"})
        assert resp.status_code == 302
