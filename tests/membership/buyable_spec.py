"""Tests for Buyable, Order, and GuildWishlistItem models."""

from decimal import Decimal

import pytest

from membership.models import Buyable, Order
from tests.core.factories import UserFactory
from tests.membership.factories import (
    BuyableFactory,
    GuildFactory,
    GuildWishlistItemFactory,
    OrderFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# GuildWishlistItem
# ---------------------------------------------------------------------------


def describe_GuildWishlistItem():
    def it_has_str_representation():
        item = GuildWishlistItemFactory(name="New Kiln")
        assert str(item) == "New Kiln"


# ---------------------------------------------------------------------------
# Buyable
# ---------------------------------------------------------------------------


def describe_Buyable():
    def it_has_str_representation():
        b = BuyableFactory(name="Open Studio Pass")
        assert str(b) == "Open Studio Pass"

    def it_auto_generates_slug_from_name():
        b = BuyableFactory(name="Kiln Firing Session")
        assert b.slug == "kiln-firing-session"

    def it_does_not_overwrite_explicit_slug():
        b = BuyableFactory(name="Something", slug="custom-slug")
        b.save()
        assert b.slug == "custom-slug"

    def it_enforces_unique_guild_slug():
        guild = GuildFactory(name="Slug Test Guild")
        BuyableFactory(guild=guild, name="Item A", slug="same-slug")
        with pytest.raises(Exception):
            BuyableFactory(guild=guild, name="Item B", slug="same-slug")

    def it_allows_same_slug_different_guilds():
        g1 = GuildFactory(name="Guild One Slug")
        g2 = GuildFactory(name="Guild Two Slug")
        BuyableFactory(guild=g1, name="Item", slug="same")
        b2 = BuyableFactory(guild=g2, name="Item", slug="same")
        assert b2.pk is not None

    def it_stores_unit_price():
        b = BuyableFactory(unit_price=Decimal("99.99"))
        b.refresh_from_db()
        assert b.unit_price == Decimal("99.99")

    def it_orders_by_name():
        guild = GuildFactory(name="Order Test Guild")
        b2 = BuyableFactory(guild=guild, name="Zebra")
        b1 = BuyableFactory(guild=guild, name="Alpha")
        assert list(Buyable.objects.filter(guild=guild)) == [b1, b2]


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------


def describe_Order():
    def it_has_str_representation():
        order = OrderFactory()
        assert f"Order #{order.pk}" in str(order)

    def it_defaults_to_pending():
        order = OrderFactory()
        assert order.status == Order.Status.PENDING

    def it_stores_amount_in_cents():
        order = OrderFactory(amount=5000)
        order.refresh_from_db()
        assert order.amount == 5000

    def it_allows_nullable_user():
        order = OrderFactory(user=None)
        assert order.user is None

    def it_tracks_fulfillment():
        user = UserFactory()
        order = OrderFactory(user=user)
        assert order.is_fulfilled is False
        assert order.fulfilled_by is None

    def it_orders_by_created_at_desc():
        o1 = OrderFactory()
        o2 = OrderFactory()
        orders = list(Order.objects.all())
        assert orders[0] == o2
        assert orders[1] == o1
