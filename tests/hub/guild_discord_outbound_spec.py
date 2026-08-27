"""BDD specs for the outbound Discord role sync wired into the in-app join/leave views.

An in-app join assigns the guild's Discord role(s); a leave removes them. All Discord HTTP
is mocked with ``respx``. Also covers the source-upgrade path (a prior Discord reaction row
is promoted by an explicit in-app join, firing the guild-lead join notice).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from factory.django import mute_signals

from core.models import SiteActivity, SiteConfiguration
from membership.models import GuildMembership
from tests.membership.factories import GuildFactory, MemberFactory

pytestmark = pytest.mark.django_db

_ROLE_RE = r"https://discord\.com/api/v10/guilds/.+/members/.+/roles/.+"


def _configure(settings):
    settings.DISCORD_BOT_TOKEN = "bot-tok"
    config = SiteConfiguration.load()
    config.discord_server_id = "srv"
    config.save()


def _linked_login(discord_user_id="u1"):
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"go_{discord_user_id}", password="pw")
    member = MemberFactory(discord_user_id=discord_user_id)
    member.user = user
    member.save(update_fields=["user", "discord_user_id"])
    client = Client()
    client.force_login(user)
    return client, member


def describe_guild_subscribe_outbound():
    @respx.mock
    def it_assigns_the_guild_role_on_subscribe(settings):
        _configure(settings)
        client, member = _linked_login()
        guild = GuildFactory(discord_role_ids=["role-1"])
        put = respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        resp = client.post(reverse("hub_guild_membership_set", args=[guild.pk]), {"joined": "on"})
        assert resp.status_code == 204
        assert put.called
        assert GuildMembership.objects.filter(guild=guild, member=member).exists()

    @respx.mock
    def it_removes_the_guild_role_on_unsubscribe(settings):
        _configure(settings)
        client, member = _linked_login()
        guild = GuildFactory(discord_role_ids=["role-1"])
        GuildMembership.objects.record_app_join(guild, member)
        delete = respx.delete(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        resp = client.post(reverse("hub_guild_membership_set", args=[guild.pk]))
        assert resp.status_code == 204
        assert delete.called
        assert not GuildMembership.objects.filter(guild=guild, member=member).exists()


def describe_membership_set_upgrade():
    @respx.mock
    def it_promotes_a_prior_reaction_row_and_fires_the_join_notice(settings):
        _configure(settings)
        client, member = _linked_login()
        guild = GuildFactory(discord_role_ids=["role-1"])
        # A prior Discord-reaction row exists (source=discord, silent — no join notice yet).
        GuildMembership.objects.record_discord_join(guild, member)
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.GUILD_JOINED).count() == 0
        respx.put(url__regex=_ROLE_RE).mock(return_value=httpx.Response(204))
        resp = client.post(reverse("hub_guild_membership_set", args=[guild.pk]), data={"joined": "on"})
        assert resp.status_code == 204
        row = GuildMembership.objects.get(guild=guild, member=member)
        assert row.source == GuildMembership.Source.APP  # promoted
        # The explicit join now fires the guild-lead join notice the silent reaction never did.
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.GUILD_JOINED).count() == 1
