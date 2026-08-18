"""Compose wizard — the general recipient checklist (form validation + view rendering, OOB, send).

The form defaults to everyone selected, validates the submission as a subset of the roster OR any
added member (dropping unknown ids without erroring), collapses "nothing changed" (or nothing
chosen) to the everyone-default, and governs ALL personal channels (bell + push + email). The
views render the checklist for a guild lead, OOB-swap it on an audience change, and honor the
chosen subset end-to-end on send.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from factory.django import mute_signals

from core.models import Notification
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
        assert set(form.fields["recipients"].initial) == values

    def it_stores_the_everyone_default_when_nothing_is_changed():
        guild = GuildFactory()
        m = _guild_member(guild, "m@example.com")
        GuildMailingListEmailFactory(guild=guild, email="c@example.com")
        data = _guild_data(guild, send_email="on", recipients=[f"user:{m.user_id}", "custom:c@example.com"])
        form = AnnouncementComposeForm(data, is_admin=True, editable_guilds=[guild])
        assert form.is_valid(), form.errors
        assert form.cleaned_data["recipient_selection"] == {}

    def it_stores_the_chosen_subset_and_drops_unknown_ids():
        guild = GuildFactory()
        m1 = _guild_member(guild, "m1@example.com")
        _guild_member(guild, "m2@example.com")  # a real second member, left unchecked
        data = _guild_data(guild, send_email="on", recipients=[f"user:{m1.user_id}", "user:999999"])
        form = AnnouncementComposeForm(data, is_admin=True, editable_guilds=[guild])
        assert form.is_valid(), form.errors
        # 999999 is not a member (dropped); m2 is simply unchecked → a real subset of one.
        assert form.cleaned_data["recipient_selection"] == {"users": [m1.user_id], "custom": []}

    def it_can_add_a_member_who_is_not_on_the_roster():
        guild = GuildFactory()
        m = _guild_member(guild, "m@example.com")
        outsider = _guild_member(GuildFactory(), "outsider@example.com")  # a member of a DIFFERENT guild
        data = _guild_data(guild, send_email="on", recipients=[f"user:{m.user_id}", f"user:{outsider.user_id}"])
        form = AnnouncementComposeForm(data, is_admin=True, editable_guilds=[guild])
        assert form.is_valid(), form.errors
        assert set(form.cleaned_data["recipient_selection"]["users"]) == {m.user_id, outsider.user_id}

    def it_treats_deselect_all_as_everyone():
        guild = GuildFactory()
        _guild_member(guild, "m@example.com")
        data = _guild_data(guild, send_email="on")  # no recipients submitted = none checked
        form = AnnouncementComposeForm(data, is_admin=True, editable_guilds=[guild])
        assert form.is_valid(), form.errors
        assert form.cleaned_data["recipient_selection"] == {}

    def it_stores_the_everyone_default_for_a_site_send():
        form = AnnouncementComposeForm(
            {"audience": "site", "title": "T", "body": "<p>x</p>", "send_email": "on", "discord_channel": "none"},
            is_admin=True,
            editable_guilds=[],
        )
        assert form.is_valid(), form.errors
        assert form.recipient_choices == []
        assert form.cleaned_data["recipient_selection"] == {}


def describe_recipient_checklist_views():
    def it_renders_the_checklist_for_a_guild_lead(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        _guild_member(guild, "weaver@example.com")
        content = client.get(reverse("hub_compose")).content.decode()
        assert 'name="recipients"' in content
        assert "compose-recipients" in content
        assert "weaver@example.com" in content
        assert "Recipients" in content

    def it_oob_swaps_the_checklist_on_an_audience_change(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        _guild_member(guild, "weaver@example.com")
        response = client.get(reverse("hub_compose_count"), {"audience": f"guild:{guild.pk}"})
        assert b"compose-recipients" in response.content
        assert b"compose-discord-picker" in response.content  # the picker still swaps too

    def it_reaches_only_the_selected_member_on_send(client: Client, mailoutbox):
        guild = GuildFactory()
        _login_lead(client, guild)
        a = _guild_member(guild, "a@example.com")
        b = _guild_member(guild, "b@example.com")
        data = _guild_data(guild, send_email="on", recipients=[f"user:{a.user_id}"])
        response = client.post(reverse("hub_compose_send"), data)
        assert response.status_code == 302
        recipients = {addr for message in mailoutbox for addr in message.to}
        assert "a@example.com" in recipients
        assert "b@example.com" not in recipients
        assert Notification.objects.filter(user=a.user, trigger="guild_announcement").exists()
        assert not Notification.objects.filter(user=b.user, trigger="guild_announcement").exists()

    def it_reports_the_sent_count_on_send(client: Client):
        guild = GuildFactory()
        _login_lead(client, guild)
        a = _guild_member(guild, "a@example.com")
        _guild_member(guild, "b@example.com")
        data = _guild_data(guild, send_email="on", recipients=[f"user:{a.user_id}"])
        response = client.post(reverse("hub_compose_send"), data, follow=True)
        body = response.content.decode()
        assert "Announcement sent to 1 member(s)." in body
