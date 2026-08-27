"""Member directory: guild affiliation is fully removed (no badges, no filter).

Guild subscriptions are a notification preference, not a public affiliation — the
directory shows no guild names, offers no guild filter, and quietly ignores stale
bookmarked ``?guild=NN`` URLs.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import GuildMembership, Member
from tests.membership.factories import GuildFactory, MemberFactory, MembershipPlanFactory


def _admin(username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pw")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


@pytest.mark.django_db
def describe_member_directory_guilds():
    def it_shows_no_guild_badges_on_cards(client: Client):
        _admin("md1")
        client.login(username="md1", password="pw")
        # Inactive guild → the sidebar never names it, so any appearance would be the badge.
        guild = GuildFactory(name="Forge Guild", is_active=False)
        GuildMembership.objects.create(guild=guild, member=MemberFactory(full_legal_name="Ada Smith"))
        resp = client.get(reverse("hub_member_directory"))
        assert b"Ada Smith" in resp.content
        assert b"Forge Guild" not in resp.content
        assert b"directory-card__guilds" not in resp.content

    def it_offers_no_guild_filter_dropdown(client: Client):
        _admin("md2")
        client.login(username="md2", password="pw")
        GuildFactory(name="Alpha Guild")
        resp = client.get(reverse("hub_member_directory"))
        assert b'name="guild"' not in resp.content
        assert b"All guilds" not in resp.content
        # The view no longer supplies the filter context (the sidebar's own guild
        # nav list is a different, page-chrome key).
        assert "guild_filter" not in resp.context

    def it_ignores_a_stale_guild_query_param(client: Client):
        _admin("md3")
        client.login(username="md3", password="pw")
        alpha = GuildFactory(name="Alpha Guild")
        beta = GuildFactory(name="Beta Guild")
        GuildMembership.objects.create(guild=alpha, member=MemberFactory(full_legal_name="Member InA"))
        GuildMembership.objects.create(guild=beta, member=MemberFactory(full_legal_name="Member InB"))
        resp = client.get(reverse("hub_member_directory"), {"guild": alpha.pk})
        # The stale param filters nothing — the full directory renders.
        assert b"Member InA" in resp.content
        assert b"Member InB" in resp.content
