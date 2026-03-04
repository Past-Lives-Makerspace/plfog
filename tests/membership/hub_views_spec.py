"""Tests for member hub views."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import Member
from tests.core.factories import UserFactory
from tests.membership.factories import (
    BuyableFactory,
    GuildFactory,
    GuildMembershipFactory,
    LeaseFactory,
    MemberFactory,
    OrderFactory,
    SpaceFactory,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# dashboard
# ---------------------------------------------------------------------------


def describe_hub_dashboard():
    def it_redirects_anonymous_users(client: Client):
        resp = client.get(reverse("hub_dashboard"))
        assert resp.status_code == 302

    def it_returns_403_for_user_without_member_record(client: Client):
        user = UserFactory()
        client.force_login(user)
        resp = client.get(reverse("hub_dashboard"))
        assert resp.status_code == 403

    def it_returns_403_for_former_member(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.FORMER)
        client.force_login(user)
        resp = client.get(reverse("hub_dashboard"))
        assert resp.status_code == 403

    def it_returns_200_for_active_member(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        client.force_login(user)
        resp = client.get(reverse("hub_dashboard"))
        assert resp.status_code == 200

    def it_uses_dashboard_template(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        client.force_login(user)
        resp = client.get(reverse("hub_dashboard"))
        assert "membership/hub/dashboard.html" in [t.name for t in resp.templates]

    def it_includes_member_in_context(client: Client):
        user = UserFactory()
        member = MemberFactory(user=user, status=Member.Status.ACTIVE)
        client.force_login(user)
        resp = client.get(reverse("hub_dashboard"))
        assert resp.context["member"] == member

    def it_includes_guild_memberships(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        guild = GuildFactory(name="My Guild", is_active=True)
        gm = GuildMembershipFactory(guild=guild, user=user)
        client.force_login(user)
        resp = client.get(reverse("hub_dashboard"))
        assert gm in list(resp.context["guild_memberships"])

    def it_includes_recent_orders(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        guild = GuildFactory(is_active=True)
        buyable = BuyableFactory(guild=guild, is_active=True)
        order = OrderFactory(buyable=buyable, user=user)
        client.force_login(user)
        resp = client.get(reverse("hub_dashboard"))
        assert order in list(resp.context["recent_orders"])

    def it_includes_active_leases(client: Client):
        user = UserFactory()
        member = MemberFactory(user=user, status=Member.Status.ACTIVE)
        space = SpaceFactory(space_id="S-HUB-1")
        today = timezone.now().date()
        lease = LeaseFactory(
            tenant_obj=member,
            space=space,
            start_date=today - timedelta(days=10),
        )
        client.force_login(user)
        resp = client.get(reverse("hub_dashboard"))
        assert lease in resp.context["active_leases"]


# ---------------------------------------------------------------------------
# my_guilds
# ---------------------------------------------------------------------------


def describe_hub_guilds():
    def it_redirects_anonymous_users(client: Client):
        resp = client.get(reverse("hub_guilds"))
        assert resp.status_code == 302

    def it_returns_403_for_user_without_member_record(client: Client):
        user = UserFactory()
        client.force_login(user)
        resp = client.get(reverse("hub_guilds"))
        assert resp.status_code == 403

    def it_returns_200_for_active_member(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        client.force_login(user)
        resp = client.get(reverse("hub_guilds"))
        assert resp.status_code == 200

    def it_uses_guilds_template(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        client.force_login(user)
        resp = client.get(reverse("hub_guilds"))
        assert "membership/hub/guilds.html" in [t.name for t in resp.templates]

    def it_shows_users_guild_memberships(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        guild = GuildFactory(name="Test Guild", is_active=True)
        gm = GuildMembershipFactory(guild=guild, user=user)
        client.force_login(user)
        resp = client.get(reverse("hub_guilds"))
        assert gm in list(resp.context["guild_memberships"])


# ---------------------------------------------------------------------------
# my_spaces
# ---------------------------------------------------------------------------


def describe_hub_spaces():
    def it_redirects_anonymous_users(client: Client):
        resp = client.get(reverse("hub_spaces"))
        assert resp.status_code == 302

    def it_returns_403_for_user_without_member_record(client: Client):
        user = UserFactory()
        client.force_login(user)
        resp = client.get(reverse("hub_spaces"))
        assert resp.status_code == 403

    def it_returns_200_for_active_member(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        client.force_login(user)
        resp = client.get(reverse("hub_spaces"))
        assert resp.status_code == 200

    def it_uses_spaces_template(client: Client):
        user = UserFactory()
        MemberFactory(user=user, status=Member.Status.ACTIVE)
        client.force_login(user)
        resp = client.get(reverse("hub_spaces"))
        assert "membership/hub/spaces.html" in [t.name for t in resp.templates]

    def it_shows_active_leases(client: Client):
        user = UserFactory()
        member = MemberFactory(user=user, status=Member.Status.ACTIVE)
        space = SpaceFactory(space_id="S-HUB-2")
        today = timezone.now().date()
        lease = LeaseFactory(
            tenant_obj=member,
            space=space,
            start_date=today - timedelta(days=10),
        )
        client.force_login(user)
        resp = client.get(reverse("hub_spaces"))
        assert lease in list(resp.context["active_leases"])

    def it_includes_member_in_context(client: Client):
        user = UserFactory()
        member = MemberFactory(user=user, status=Member.Status.ACTIVE)
        client.force_login(user)
        resp = client.get(reverse("hub_spaces"))
        assert resp.context["member"] == member


# ---------------------------------------------------------------------------
# Redirects from old URLs
# ---------------------------------------------------------------------------


def describe_old_url_redirects():
    def it_redirects_account_profile_to_hub(client: Client):
        resp = client.get("/account/profile/")
        assert resp.status_code == 301
        assert resp["Location"] == "/member-hub/profile/"

    def it_redirects_account_orders_to_hub(client: Client):
        resp = client.get("/account/orders/")
        assert resp.status_code == 301
        assert resp["Location"] == "/member-hub/orders/"

    def it_redirects_guild_manage_to_hub(client: Client):
        guild = GuildFactory(name="Redirect Guild", is_active=True)
        resp = client.get(f"/guilds/{guild.slug}/manage/")
        assert resp.status_code == 301
        assert resp["Location"] == f"/member-hub/manage/{guild.slug}/"

    def it_redirects_guild_manage_orders_to_hub(client: Client):
        guild = GuildFactory(name="Redirect Orders Guild", is_active=True)
        resp = client.get(f"/guilds/{guild.slug}/manage/orders/")
        assert resp.status_code == 301
        assert resp["Location"] == f"/member-hub/manage/{guild.slug}/orders/"
