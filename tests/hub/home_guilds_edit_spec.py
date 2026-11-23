"""BDD specs for Home's Edit Your Guilds (#1318): the Edit button, the modal's join switches,
and the Settings switch endpoint answering ``surface=home`` with Home's words and chips."""

from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from membership.models import GuildMembership, Member
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db


def _linked(client: Client, username: str = "hge") -> Member:
    """A logged-in user with a linked Member (a plan must exist first)."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    client.login(username=username, password="pass")
    return user.member


def _toast(response) -> dict:
    return json.loads(response["HX-Trigger"])["showToast"]


def _switch(html: str, guild_pk: int) -> str:
    match = re.search(rf"<input\b[^>]*/settings/guilds/{guild_pk}/[^>]*>", html)
    assert match is not None, f"no switch rendered for guild {guild_pk}"
    return match.group(0)


def _set(client: Client, guild_pk: int, *, joined: bool):
    data = {"surface": "home", **({"joined": "on"} if joined else {})}
    return client.post(reverse("hub_guild_membership_set", args=[guild_pk]), data=data)


def describe_home_your_guilds_card():
    def it_has_an_edit_button_that_opens_the_modal(client: Client):
        _linked(client)
        html = client.get(reverse("hub_home")).content.decode()
        assert 'data-guidance-target="home.guilds-edit"' in html
        assert "$dispatch('open-modal', 'home-guilds-edit')" in html
        assert 'id="home-guilds-edit-title">Edit Your Guilds</h2>' in html

    def it_shows_edit_with_no_guilds_joined(client: Client):
        _linked(client)
        html = client.get(reverse("hub_home")).content.decode()
        assert 'data-guidance-target="home.guilds-edit"' in html
        assert "Press Edit to join one" in html

    def it_lists_every_active_guild_with_a_join_switch_on_for_joined_ones(client: Client):
        member = _linked(client)
        joined = GuildFactory(name="Zeta Joined")
        other = GuildFactory(name="Alpha Other")
        hidden = GuildFactory(name="Hidden Guild", is_active=False)
        GuildMembershipFactory(guild=joined, member=member)

        response = client.get(reverse("hub_home"))
        html = response.content.decode()

        assert [row.guild for row in response.context["guild_edit_rows"]] == [other, joined]
        assert " checked" in _switch(html, joined.pk)
        assert " checked" not in _switch(html, other.pk)
        assert 'aria-label="Join Alpha Other"' in _switch(html, other.pk)
        assert '"surface": "home"' in _switch(html, other.pk)
        assert f"/settings/guilds/{hidden.pk}/" not in html

    def it_shows_the_empty_line_when_no_guild_is_set_up(client: Client):
        _linked(client)
        response = client.get(reverse("hub_home"))
        assert response.context["guild_edit_rows"] == []
        assert b"No guilds have been set up yet." in response.content


def describe_guild_membership_set_from_home():
    def it_joins_through_the_settings_path_and_redraws_the_chips(client: Client):
        member = _linked(client)
        guild = GuildFactory(name="Wood Shop")
        with (
            patch("membership.orientations.member_joined_guild") as joined,
            patch("membership.models.Member.send_guild_welcome") as welcome,
        ):
            response = _set(client, guild.pk, joined=True)

        assert response.status_code == 200
        assert GuildMembership.objects.filter(guild=guild, member=member).exists()
        joined.assert_called_once_with(guild, member)
        welcome.assert_not_called()
        assert _toast(response) == {"message": "You joined Wood Shop.", "type": "success"}
        html = response.content.decode()
        assert '<div id="home-my-guilds" hx-swap-oob="true">' in html
        assert 'pl-guild-chip__name">Wood Shop<' in html
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is not None

    def it_leaves_and_redraws_the_empty_chips(client: Client):
        member = _linked(client)
        guild = GuildFactory(name="Metals")
        GuildMembershipFactory(guild=guild, member=member)
        with patch("core.events.discord_roles.on_membership_changed") as roles:
            response = _set(client, guild.pk, joined=False)

        assert response.status_code == 200
        assert not GuildMembership.objects.filter(guild=guild, member=member).exists()
        roles.assert_called_once_with(guild, member, joined=False)
        assert _toast(response) == {"message": "You left Metals. You can rejoin any time.", "type": "info"}
        assert "You haven't joined any guilds yet" in response.content.decode()

    def it_refuses_an_unlinked_account(client: Client):
        user = User.objects.create_user(username="nolink", password="pass")
        Member.objects.filter(user=user).delete()
        client.login(username="nolink", password="pass")
        guild = GuildFactory()

        response = _set(client, guild.pk, joined=True)

        assert response.status_code == 204
        assert _toast(response)["type"] == "error"
        assert not GuildMembership.objects.filter(guild=guild).exists()
