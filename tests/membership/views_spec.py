"""Tests for membership views."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from membership.models import Buyable
from tests.core.factories import UserFactory
from tests.membership.factories import (
    BuyableFactory,
    GuildFactory,
    GuildWishlistItemFactory,
    LeaseFactory,
    MemberFactory,
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
