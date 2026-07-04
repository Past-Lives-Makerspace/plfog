"""BDD specs for the "My Guilds" settings tab.

Covers the grid-data service (``build_my_guilds_rows``), the join/leave HTMX endpoint
(``guild_membership_set``), the Guilds tab wiring in ``user_settings``, the rendered
toggle grid, and the guild-page "you're in this guild" touch.
"""

from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client
from django.urls import reverse

from core.models import SiteActivity
from hub.guild_membership import build_my_guilds_rows
from membership.models import GuildMembership, Member
from tests.membership.factories import (
    GuildFactory,
    GuildMembershipFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _linked_user(client: Client, username: str = "u1") -> tuple[User, Member]:
    """Create a user with an auto-linked Member (a plan must exist first) and log in."""
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass", email=f"{username}@example.com")
    client.login(username=username, password="pass")
    return user, user.member


def _unlinked_user(client: Client, username: str = "nomember") -> User:
    """Create a logged-in user with no linked Member (unlinked account)."""
    user = User.objects.create_user(username=username, password="pass")
    Member.objects.filter(user=user).delete()
    client.login(username=username, password="pass")
    return user


def _toast(response) -> dict:
    """Parse the ``showToast`` payload from an HTMX response's HX-Trigger header."""
    return json.loads(response["HX-Trigger"])["showToast"]


def _toggle_input(html: str, guild_pk: int) -> str:
    """Return the raw ``<input>`` toggle tag for one guild's row in rendered HTML."""
    match = re.search(rf"<input\b[^>]*/settings/guilds/{guild_pk}/[^>]*>", html)
    assert match is not None, f"no toggle input rendered for guild {guild_pk}"
    return match.group(0)


def describe_build_my_guilds_rows():
    def it_returns_one_row_per_active_guild_ordered_by_name():
        member = MemberFactory()
        GuildFactory(name="Beta")
        GuildFactory(name="Alpha")
        rows = build_my_guilds_rows(member)
        assert [row.guild.name for row in rows] == ["Alpha", "Beta"]

    def it_excludes_inactive_and_soft_deleted_guilds():
        member = MemberFactory()
        GuildFactory(name="Active")
        GuildFactory(name="Inactive", is_active=False)
        GuildFactory(name="Deleted").soft_delete()
        assert [row.guild.name for row in build_my_guilds_rows(member)] == ["Active"]

    def it_flags_joined_only_for_guilds_the_member_is_in():
        member = MemberFactory()
        joined = GuildFactory(name="Joined")
        GuildFactory(name="NotJoined")
        GuildMembershipFactory(guild=joined, member=member)
        by_name = {row.guild.name: row.joined for row in build_my_guilds_rows(member)}
        assert by_name == {"Joined": True, "NotJoined": False}

    def it_uses_the_meeting_schedule_as_the_hint():
        member = MemberFactory()
        GuildFactory(name="Woodshop", meeting_schedule="Tuesdays 6pm, Studio B")
        assert build_my_guilds_rows(member)[0].meeting_hint == "Tuesdays 6pm, Studio B"

    def it_returns_empty_for_an_unlinked_member():
        GuildFactory()
        assert build_my_guilds_rows(None) == []

    def it_avoids_n_plus_one_queries(django_assert_num_queries):
        member = MemberFactory()
        first = GuildFactory(name="One")
        GuildFactory(name="Two")
        GuildFactory(name="Three")
        GuildMembershipFactory(guild=first, member=member)
        # One query for the joined-id set, one for the guilds — attribute access on the
        # loaded rows adds none.
        with django_assert_num_queries(2):
            rows = build_my_guilds_rows(member)
            [(row.guild.name, row.joined, row.meeting_hint) for row in rows]


def describe_guild_membership_set():
    def it_creates_the_membership_and_fires_the_join_side_effect(client: Client):
        _user, member = _linked_user(client)
        guild = GuildFactory()
        with patch("membership.orientations.member_joined_guild") as joined:
            response = client.post(reverse("hub_guild_membership_set", args=[guild.pk]), data={"joined": "on"})
        assert response.status_code == 204
        assert GuildMembership.objects.filter(guild=guild, member=member).exists()
        joined.assert_called_once_with(guild, member)
        assert _toast(response)["type"] == "success"
        assert f"You joined {guild.name}." == _toast(response)["message"]

    def it_is_idempotent_and_does_not_refire_when_already_joined(client: Client):
        _user, member = _linked_user(client)
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=member)
        with patch("membership.orientations.member_joined_guild") as joined:
            response = client.post(reverse("hub_guild_membership_set", args=[guild.pk]), data={"joined": "on"})
        assert response.status_code == 204
        assert GuildMembership.objects.filter(guild=guild, member=member).count() == 1
        joined.assert_not_called()

    def it_leaves_when_the_joined_field_is_absent(client: Client):
        _user, member = _linked_user(client)
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=member)
        response = client.post(reverse("hub_guild_membership_set", args=[guild.pk]), data={})
        assert response.status_code == 204
        assert not GuildMembership.objects.filter(guild=guild, member=member).exists()
        toast = _toast(response)
        assert toast["type"] == "info"
        assert "You left" in toast["message"] and "rejoin anytime" in toast["message"]

    def it_is_idempotent_on_leave_when_not_a_member(client: Client):
        _user, _member = _linked_user(client)
        guild = GuildFactory()
        response = client.post(reverse("hub_guild_membership_set", args=[guild.pk]), data={})
        assert response.status_code == 204
        assert GuildMembership.objects.count() == 0

    def it_returns_an_error_toast_for_an_unlinked_account(client: Client):
        _unlinked_user(client)
        guild = GuildFactory()
        response = client.post(reverse("hub_guild_membership_set", args=[guild.pk]), data={"joined": "on"})
        assert response.status_code == 204
        assert GuildMembership.objects.count() == 0
        assert _toast(response)["type"] == "error"

    def it_rejects_a_get_request(client: Client):
        _linked_user(client)
        guild = GuildFactory()
        assert client.get(reverse("hub_guild_membership_set", args=[guild.pk])).status_code == 405

    def it_redirects_an_anonymous_request_to_login(client: Client):
        guild = GuildFactory()
        response = client.post(reverse("hub_guild_membership_set", args=[guild.pk]), data={"joined": "on"})
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_404s_an_unknown_guild(client: Client):
        _linked_user(client)
        response = client.post(reverse("hub_guild_membership_set", args=[999999]), data={"joined": "on"})
        assert response.status_code == 404


def describe_leave_then_rejoin():
    def it_does_not_resend_the_welcome_email_on_rejoin(client: Client):
        _user, _member = _linked_user(client)
        guild = GuildFactory()
        GuildOrientationSettingsFactory(
            guild=guild,
            is_enabled=True,
            join_email_enabled=True,
            join_email_subject="Welcome to the guild!",
            join_email_body="So glad you're here.",
        )
        url = reverse("hub_guild_membership_set", args=[guild.pk])
        client.post(url, data={"joined": "on"})  # join → welcome email
        client.post(url, data={})  # leave
        client.post(url, data={"joined": "on"})  # rejoin — period dedupe suppresses a 2nd email
        assert sum(1 for m in mail.outbox if m.subject == "Welcome to the guild!") == 1

    def it_refires_the_join_activity_on_each_rejoin(client: Client):
        # Documented decision (§9): a rejoin's get_or_create returns created=True, so the
        # non-period-guarded side effects (activity row, lead notice) fire again — an
        # accepted duplicate matching today's full-page join view.
        _user, _member = _linked_user(client)
        guild = GuildFactory()
        url = reverse("hub_guild_membership_set", args=[guild.pk])
        client.post(url, data={"joined": "on"})
        client.post(url, data={})
        client.post(url, data={"joined": "on"})
        assert SiteActivity.objects.filter(kind=SiteActivity.Kind.GUILD_JOINED).count() == 2


def describe_user_settings_guilds_tab():
    def it_sets_active_tab_to_guilds_for_the_deep_link(client: Client):
        _linked_user(client)
        response = client.get(reverse("hub_user_settings") + "?tab=guilds")
        assert response.context["active_tab"] == "guilds"

    def it_falls_back_to_profile_for_a_bogus_tab(client: Client):
        _linked_user(client)
        response = client.get(reverse("hub_user_settings") + "?tab=bogus")
        assert response.context["active_tab"] == "profile"

    def it_passes_the_guild_rows_in_context(client: Client):
        _linked_user(client)
        GuildFactory(name="Ceramics")
        response = client.get(reverse("hub_user_settings") + "?tab=guilds")
        assert [row.guild.name for row in response.context["my_guilds_rows"]] == ["Ceramics"]


def describe_guilds_tab_template():
    def it_pre_checks_joined_guilds_and_leaves_others_unchecked(client: Client):
        _user, member = _linked_user(client)
        joined = GuildFactory(name="In This One")
        other = GuildFactory(name="Not This One")
        GuildMembershipFactory(guild=joined, member=member)
        html = client.get(reverse("hub_user_settings") + "?tab=guilds").content.decode()
        assert "checked" in _toggle_input(html, joined.pk)
        assert "checked" not in _toggle_input(html, other.pk)

    def it_wires_each_toggle_to_the_membership_endpoint(client: Client):
        _linked_user(client)
        guild = GuildFactory()
        html = client.get(reverse("hub_user_settings") + "?tab=guilds").content.decode()
        tag = _toggle_input(html, guild.pk)
        assert 'hx-trigger="change"' in tag
        assert 'hx-disabled-elt="this"' in tag

    def it_shows_the_empty_message_when_no_guilds_exist(client: Client):
        _linked_user(client)
        html = client.get(reverse("hub_user_settings") + "?tab=guilds").content.decode()
        assert "No guilds have been set up yet" in html

    def it_shows_the_unlinked_message_and_no_toggles_for_an_account_with_no_member(client: Client):
        _unlinked_user(client)
        GuildFactory()
        html = client.get(reverse("hub_user_settings") + "?tab=guilds").content.decode()
        assert "not linked to a membership. Contact an admin for help." in html
        assert "/settings/guilds/" not in html


def describe_guild_detail_membership_touch():
    def it_shows_youre_in_this_guild_with_a_manage_link_for_a_member(client: Client):
        _user, member = _linked_user(client)
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=member)
        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "You're in this guild" in html
        assert reverse("hub_user_settings") + "?tab=guilds" in html
        assert "Manage in Settings" in html

    def it_still_shows_join_for_a_member_not_in_the_guild(client: Client):
        _linked_user(client)
        guild = GuildFactory()
        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "Join This Guild" in html
        assert "You're in this guild" not in html
