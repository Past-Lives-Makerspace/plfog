"""BDD specs for the settings Guild Updates tab (the ``guilds`` tab).

Covers the grid-data service (``build_my_guilds_rows``), the subscribe/unsubscribe
HTMX endpoint (``guild_membership_set``) including its answered-stamp, the Guilds tab
wiring in ``user_settings`` (deep-link stamp included), the rendered toggle grid, and
the guild-page subscription touch.
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
    def it_creates_the_subscription_and_fires_the_welcome_side_effect(client: Client):
        _user, member = _linked_user(client)
        guild = GuildFactory()
        with patch("membership.orientations.member_joined_guild") as joined:
            response = client.post(reverse("hub_guild_membership_set", args=[guild.pk]), data={"joined": "on"})
        assert response.status_code == 204
        assert GuildMembership.objects.filter(guild=guild, member=member).exists()
        joined.assert_called_once_with(guild, member)
        assert _toast(response)["type"] == "success"
        assert f"You'll get updates from {guild.name}." == _toast(response)["message"]

    def it_is_idempotent_and_does_not_refire_when_already_joined(client: Client):
        _user, member = _linked_user(client)
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=member)
        with patch("membership.orientations.member_joined_guild") as joined:
            response = client.post(reverse("hub_guild_membership_set", args=[guild.pk]), data={"joined": "on"})
        assert response.status_code == 204
        assert GuildMembership.objects.filter(guild=guild, member=member).count() == 1
        joined.assert_not_called()

    def it_unsubscribes_when_the_joined_field_is_absent(client: Client):
        _user, member = _linked_user(client)
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=member)
        response = client.post(reverse("hub_guild_membership_set", args=[guild.pk]), data={})
        assert response.status_code == 204
        assert not GuildMembership.objects.filter(guild=guild, member=member).exists()
        toast = _toast(response)
        assert toast["type"] == "info"
        assert "You won't get updates from" in toast["message"]
        assert "Turn them back on anytime." in toast["message"]

    def it_stamps_the_guild_updates_answer_on_subscribe_and_unsubscribe(client: Client):
        _user, member = _linked_user(client)
        guild = GuildFactory()
        assert member.guild_updates_prompt_answered_at is None
        client.post(reverse("hub_guild_membership_set", args=[guild.pk]), data={"joined": "on"})
        member.refresh_from_db()
        first_stamp = member.guild_updates_prompt_answered_at
        assert first_stamp is not None
        client.post(reverse("hub_guild_membership_set", args=[guild.pk]), data={})
        member.refresh_from_db()
        # The stamp is one-way — a later flip never overwrites it.
        assert member.guild_updates_prompt_answered_at == first_stamp

    def it_stamps_a_legacy_member_who_unsubscribes_their_last_guild(client: Client):
        # The leave-last-guild edge: without the stamp this member (no stamp, zero rows)
        # would become prompt-eligible again right after deliberately choosing none.
        _user, member = _linked_user(client)
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=member)
        assert member.guild_updates_prompt_answered_at is None
        client.post(reverse("hub_guild_membership_set", args=[guild.pk]), data={})
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is not None
        assert not member.needs_guild_updates_prompt

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

    def it_stamps_the_guild_updates_answer_on_the_deep_link(client: Client):
        # The dead-end regression: a member who wants updates from zero guilds completes
        # the "Choose your guild updates" checklist step from the exact page it points at.
        _user, member = _linked_user(client)
        assert member.guild_updates_prompt_answered_at is None
        client.get(reverse("hub_user_settings") + "?tab=guilds")
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is not None
        step = next(s for s in member.onboarding.steps if s.key == "guilds")
        assert step.done is True

    def it_does_not_stamp_on_other_tabs(client: Client):
        # A bare /settings/ now lands on (and stamps) the Guilds default tab, so only the
        # explicit non-guilds tabs must leave the answer unstamped.
        _user, member = _linked_user(client)
        client.get(reverse("hub_user_settings") + "?tab=notifications")
        client.get(reverse("hub_user_settings") + "?tab=profile")
        member.refresh_from_db()
        assert member.guild_updates_prompt_answered_at is None

    def it_survives_the_deep_link_for_an_unlinked_account(client: Client):
        _unlinked_user(client, "nomember_tab")
        response = client.get(reverse("hub_user_settings") + "?tab=guilds")
        assert response.status_code == 200

    def it_falls_back_to_guilds_for_a_bogus_tab(client: Client):
        _linked_user(client)
        response = client.get(reverse("hub_user_settings") + "?tab=bogus")
        assert response.context["active_tab"] == "guilds"

    def it_passes_the_guild_rows_in_context(client: Client):
        _linked_user(client)
        GuildFactory(name="Ceramics")
        response = client.get(reverse("hub_user_settings") + "?tab=guilds")
        assert [row.guild.name for row in response.context["my_guilds_rows"]] == ["Ceramics"]


def describe_guild_announcement_matrix_copy():
    SURFACE_NEUTRAL = "A guild you follow posted an announcement. Pick which guilds in your hub Settings."

    def it_renders_the_follow_description_on_the_notifications_tab(client: Client):
        _linked_user(client)
        html = client.get(reverse("hub_user_settings") + "?tab=notifications").content.decode()
        assert SURFACE_NEUTRAL in html

    def it_renders_the_same_description_on_the_token_no_login_prefs_page(client: Client):
        # The description is deliberately surface-neutral: this page has no Guilds tab,
        # and "in your hub Settings" stays true from here too.
        from core.email_prefs import make_prefs_token

        MembershipPlanFactory()
        user = User.objects.create_user(username="tok", password="pass", email="tok@example.com")
        response = client.get(reverse("hub_user_settings"), {"t": make_prefs_token(user)})
        assert response.status_code == 200
        assert SURFACE_NEUTRAL in response.content.decode()


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

    def it_renders_the_guild_updates_heading_explainer_and_notifications_cross_link(client: Client):
        _linked_user(client)
        GuildFactory(name="Ceramics")
        html = client.get(reverse("hub_user_settings") + "?tab=guilds").content.decode()
        assert "Guild Updates" in html
        assert "Choose which guilds you get announcements from." in html
        assert "Flip one on to follow it, off to stop." in html
        assert "How updates reach you (email, in app, Discord) is set on the" in html
        assert "Notifications tab" in html

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


def describe_guild_detail_subscription_touch():
    def it_shows_the_updates_line_with_a_manage_link_for_a_subscriber(client: Client):
        _user, member = _linked_user(client)
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=member)
        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "You get this guild's updates" in html
        assert reverse("hub_user_settings") + "?tab=guilds" in html
        assert "Manage in Settings" in html

    def it_points_a_non_subscriber_at_settings_instead_of_a_join_button(client: Client):
        _linked_user(client)
        guild = GuildFactory()
        html = client.get(reverse("hub_guild_detail", args=[guild.slug])).content.decode()
        assert "Join This Guild" not in html
        assert "Want announcements from this guild?" in html
        assert reverse("hub_user_settings") + "?tab=guilds" in html
