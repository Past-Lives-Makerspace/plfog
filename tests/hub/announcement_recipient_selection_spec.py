"""Compose wizard — the email recipient checklist (form validation + view rendering, OOB, send).

The form defaults to everyone selected, validates the submission as a subset of the live roster
(dropping stale ids without erroring), collapses "nothing deselected" to the everyone-default,
and rejects a guild deselect-all while email is on. The views render the checklist for a guild
lead, OOB-swap it on an audience change, and honor the chosen subset end-to-end on send.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from hub.forms import AnnouncementComposeForm, announcement_recipient_choices
from tests.membership.factories import (
    GuildFactory,
    GuildMailingListEmailFactory,
    GuildMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db

_seq = {"n": 0}


def _guild_member(guild, email: str):
    _seq["n"] += 1
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"rselv_{_seq['n']}", email=email, last_login=timezone.now())
    member.user = user
    member.save(update_fields=["user"])
    GuildMembershipFactory(guild=guild, member=member)
    return member


def _login_lead(client: Client, guild, username: str = "lead"):
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, email=f"{username}@x.com", password="p")
    member = user.member
    guild.guild_lead = member
    guild.save(update_fields=["guild_lead"])
    client.login(username=username, password="p")
    return user, member


def _guild_data(guild, **overrides) -> dict:
    data = {
        "audience": f"guild:{guild.pk}",
        "title": "Heads up",
        "body": "<p>Hello.</p>",
        "discord_channel": "none",
        "mention": "none",
        "draft_pk": "",
    }
    data.update(overrides)
    return data


def describe_announcement_recipient_choices():
    def it_is_empty_for_a_site_audience():
        assert announcement_recipient_choices("site", None) == []

    def it_lists_members_then_custom_addresses_for_a_guild():
        guild = GuildFactory()
        m = _guild_member(guild, "m@example.com")
        GuildMailingListEmailFactory(guild=guild, email="c@example.com")
        values = [value for value, _label in announcement_recipient_choices("guild", guild)]
        assert f"user:{m.user_id}" in values
        assert "custom:c@example.com" in values


def describe_recipient_checklist_form():
    def it_defaults_to_all_recipients_selected():
        guild = GuildFactory()
        m = _guild_member(guild, "m@example.com")
        GuildMailingListEmailFactory(guild=guild, email="c@example.com")
        form = AnnouncementComposeForm(
            is_admin=True, editable_guilds=[guild], initial={"audience": f"guild:{guild.pk}"}
        )
        values = {value for value, _label in form.recipient_choices}
        assert values == {f"user:{m.user_id}", "custom:c@example.com"}
        assert set(form.fields["email_recipients"].initial) == values

    def it_stores_the_everyone_default_when_nothing_is_deselected():
        guild = GuildFactory()
        m = _guild_member(guild, "m@example.com")
        GuildMailingListEmailFactory(guild=guild, email="c@example.com")
        data = _guild_data(guild, send_email="on", email_recipients=[f"user:{m.user_id}", "custom:c@example.com"])
        form = AnnouncementComposeForm(data, is_admin=True, editable_guilds=[guild])
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email_recipient_selection"] == {}

    def it_stores_the_chosen_subset_and_drops_stale_ids():
        guild = GuildFactory()
        m1 = _guild_member(guild, "m1@example.com")
        _guild_member(guild, "m2@example.com")  # a real second member, left unchecked
        data = _guild_data(guild, send_email="on", email_recipients=[f"user:{m1.user_id}", "user:999999"])
        form = AnnouncementComposeForm(data, is_admin=True, editable_guilds=[guild])
        assert form.is_valid(), form.errors
        # 999999 is dropped (not in the roster); m2 is simply unchecked → a real subset.
        assert form.cleaned_data["email_recipient_selection"] == {"users": [m1.user_id], "custom": []}

    def it_errors_on_deselect_all_with_email_on_for_a_guild():
        guild = GuildFactory()
        _guild_member(guild, "m@example.com")
        data = _guild_data(guild, send_email="on")  # no email_recipients submitted = none checked
        form = AnnouncementComposeForm(data, is_admin=True, editable_guilds=[guild])
        assert not form.is_valid()
        assert any("Pick at least one email recipient" in error for error in form.non_field_errors())

    def it_allows_deselect_all_when_email_is_off():
        guild = GuildFactory()
        _guild_member(guild, "m@example.com")
        data = _guild_data(guild)  # send_email omitted = off, no recipients
        form = AnnouncementComposeForm(data, is_admin=True, editable_guilds=[guild])
        assert form.is_valid(), form.errors
        assert form.cleaned_data["email_recipient_selection"] == {"users": [], "custom": []}

    def it_never_trips_the_error_for_a_site_send():
        form = AnnouncementComposeForm(
            {"audience": "site", "title": "T", "body": "<p>x</p>", "send_email": "on", "discord_channel": "none"},
            is_admin=True,
            editable_guilds=[],
        )
        assert form.is_valid(), form.errors
        assert form.recipient_choices == []
        assert form.cleaned_data["email_recipient_selection"] == {}


def describe_recipient_checklist_views():
    def it_renders_the_checklist_for_a_guild_lead(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        _guild_member(guild, "weaver@example.com")
        content = client.get(reverse("hub_compose")).content.decode()
        assert 'name="email_recipients"' in content
        assert "compose-recipients" in content
        assert "weaver@example.com" in content
        assert "Email recipients" in content

    def it_oob_swaps_the_checklist_on_an_audience_change(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        _guild_member(guild, "weaver@example.com")
        response = client.get(reverse("hub_compose_count"), {"audience": f"guild:{guild.pk}"})
        assert b"compose-recipients" in response.content
        assert b"compose-discord-picker" in response.content  # the picker still swaps too

    def it_emails_only_the_selected_member_on_send(client: Client, mailoutbox):
        guild = GuildFactory()
        _login_lead(client, guild)
        a = _guild_member(guild, "a@example.com")
        _guild_member(guild, "b@example.com")
        data = _guild_data(guild, send_email="on", email_recipients=[f"user:{a.user_id}"])
        response = client.post(reverse("hub_compose_send"), data)
        assert response.status_code == 302
        recipients = {addr for message in mailoutbox for addr in message.to}
        assert "a@example.com" in recipients
        assert "b@example.com" not in recipients

    def it_reports_the_emailed_of_total_message_on_send(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        a = _guild_member(guild, "a@example.com")
        _guild_member(guild, "b@example.com")
        data = _guild_data(guild, send_email="on", email_recipients=[f"user:{a.user_id}"])
        response = client.post(reverse("hub_compose_send"), data, follow=True)
        body = response.content.decode()
        assert "Emailed 1 of 2" in body
        assert "everyone sees it in the app" in body
