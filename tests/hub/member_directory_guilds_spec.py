"""Member directory: guild affiliation badges + guild filter (revived with Join This Guild).

A member's joined guilds are shown as badges on their directory card, and a guild filter
dropdown narrows the grid to one guild's members. Badges cover only active guilds, respect
directory privacy, and are N+1 free. An unknown or stale ``?guild=`` value is ignored.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
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
    def it_shows_a_guild_badge_on_a_members_card(client: Client):
        _admin("md1")
        client.login(username="md1", password="pw")
        guild = GuildFactory(name="Forge Guild", is_active=True)
        GuildMembership.objects.create(guild=guild, member=MemberFactory(full_legal_name="Ada Smith"))
        resp = client.get(reverse("hub_member_directory"))
        assert b"Ada Smith" in resp.content
        assert b"Forge Guild" in resp.content
        assert b"pl-directory-guild" in resp.content

    def it_links_a_badge_to_the_guild_filter(client: Client):
        _admin("md1b")
        client.login(username="md1b", password="pw")
        guild = GuildFactory(name="Forge Guild", is_active=True)
        GuildMembership.objects.create(guild=guild, member=MemberFactory(full_legal_name="Ada Smith"))
        resp = client.get(reverse("hub_member_directory"))
        assert f"?guild={guild.slug}".encode() in resp.content

    def it_hides_badges_for_inactive_guilds(client: Client):
        _admin("md1c")
        client.login(username="md1c", password="pw")
        guild = GuildFactory(name="Ghost Guild", is_active=False)
        GuildMembership.objects.create(guild=guild, member=MemberFactory(full_legal_name="Ada Smith"))
        resp = client.get(reverse("hub_member_directory"))
        assert b"Ada Smith" in resp.content
        assert b"Ghost Guild" not in resp.content

    def it_offers_a_guild_filter_dropdown(client: Client):
        _admin("md2")
        client.login(username="md2", password="pw")
        GuildFactory(name="Alpha Guild", is_active=True)
        resp = client.get(reverse("hub_member_directory"))
        assert b'name="guild"' in resp.content
        assert b"All guilds" in resp.content
        assert b"Alpha Guild" in resp.content

    def it_filters_members_to_the_selected_guild(client: Client):
        _admin("md3")
        client.login(username="md3", password="pw")
        alpha = GuildFactory(name="Alpha Guild", is_active=True)
        beta = GuildFactory(name="Beta Guild", is_active=True)
        GuildMembership.objects.create(guild=alpha, member=MemberFactory(full_legal_name="Member InA"))
        GuildMembership.objects.create(guild=beta, member=MemberFactory(full_legal_name="Member InB"))
        resp = client.get(reverse("hub_member_directory"), {"guild": alpha.slug})
        assert b"Member InA" in resp.content
        assert b"Member InB" not in resp.content
        assert resp.context["selected_guild"] == alpha

    def it_ignores_an_unknown_guild_slug(client: Client):
        _admin("md4")
        client.login(username="md4", password="pw")
        alpha = GuildFactory(name="Alpha Guild", is_active=True)
        GuildMembership.objects.create(guild=alpha, member=MemberFactory(full_legal_name="Member InA"))
        resp = client.get(reverse("hub_member_directory"), {"guild": "does-not-exist"})
        assert b"Member InA" in resp.content
        assert resp.context["selected_guild"] is None

    def it_ignores_a_stale_numeric_guild_param(client: Client):
        _admin("md5")
        client.login(username="md5", password="pw")
        alpha = GuildFactory(name="Alpha Guild", is_active=True)
        beta = GuildFactory(name="Beta Guild", is_active=True)
        GuildMembership.objects.create(guild=alpha, member=MemberFactory(full_legal_name="Member InA"))
        GuildMembership.objects.create(guild=beta, member=MemberFactory(full_legal_name="Member InB"))
        resp = client.get(reverse("hub_member_directory"), {"guild": alpha.pk})
        assert b"Member InA" in resp.content
        assert b"Member InB" in resp.content

    def it_shows_the_empty_state_when_no_member_matches(client: Client):
        _admin("md6")
        client.login(username="md6", password="pw")
        empty_guild = GuildFactory(name="Lonely Guild", is_active=True)
        resp = client.get(reverse("hub_member_directory"), {"guild": empty_guild.slug})
        assert b"No members match this filter." in resp.content

    def it_renders_badges_without_n_plus_1(client: Client):
        # The badges must come from ONE prefetch query, not one lookup per card: with 20
        # guild-affiliated members, a per-card N+1 would fire ~20 GuildMembership selects.
        _admin("mdn")
        client.login(username="mdn", password="pw")
        g1 = GuildFactory(name="G One", is_active=True)
        g2 = GuildFactory(name="G Two", is_active=True)
        for i in range(20):
            member = MemberFactory(full_legal_name=f"Person {i}")
            GuildMembership.objects.create(guild=g1, member=member)
            GuildMembership.objects.create(guild=g2, member=member)
        with CaptureQueriesContext(connection) as captured:
            client.get(reverse("hub_member_directory"))
        membership_queries = [q for q in captured.captured_queries if "guildmembership" in q["sql"].lower()]
        assert len(membership_queries) <= 2
