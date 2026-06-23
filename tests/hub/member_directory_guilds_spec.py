"""Member directory: guild badges on cards + filter by guild."""

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
    def it_shows_a_members_guild_badges(client: Client):
        _admin("md1")
        client.login(username="md1", password="pw")
        # Inactive guild → its name only appears via the membership badge, not the filter dropdown.
        guild = GuildFactory(name="Forge Guild", is_active=False)
        GuildMembership.objects.create(guild=guild, member=MemberFactory(full_legal_name="Ada Smith"))
        resp = client.get(reverse("hub_member_directory"))
        assert b"Forge Guild" in resp.content

    def it_filters_members_by_guild(client: Client):
        _admin("md2")
        client.login(username="md2", password="pw")
        alpha = GuildFactory(name="Alpha Guild")
        beta = GuildFactory(name="Beta Guild")
        GuildMembership.objects.create(guild=alpha, member=MemberFactory(full_legal_name="Member InA"))
        GuildMembership.objects.create(guild=beta, member=MemberFactory(full_legal_name="Member InB"))
        resp = client.get(reverse("hub_member_directory"), {"guild": alpha.pk})
        assert b"Member InA" in resp.content
        assert b"Member InB" not in resp.content
