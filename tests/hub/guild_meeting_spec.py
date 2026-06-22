"""Guild meeting config — GuildEditForm save + the Next Meeting card on the guild page."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from hub.forms import GuildEditForm
from membership.models import Guild, Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory


def _editor(username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pw")
    member = user.member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return user


@pytest.mark.django_db
def describe_meeting_config():
    def it_saves_structured_recurrence_via_the_form():
        guild = GuildFactory()
        form = GuildEditForm(
            {
                "name": guild.name,
                "meeting_cadence": Guild.MeetingCadence.MONTHLY,
                "meeting_weekday": "3",
                "meeting_week_of_month": "3",
            },
            instance=guild,
        )
        assert form.is_valid(), form.errors
        saved = form.save()
        assert saved.meeting_cadence == Guild.MeetingCadence.MONTHLY
        assert saved.meeting_weekday == 3
        assert saved.meeting_week_of_month == 3

    def it_defaults_a_blank_cadence_to_none():
        guild = GuildFactory()
        form = GuildEditForm({"name": guild.name}, instance=guild)
        assert form.is_valid(), form.errors
        assert form.save().meeting_cadence == Guild.MeetingCadence.NONE

    def it_shows_the_next_meeting_on_the_guild_page(client: Client):
        _editor("mtg")
        client.login(username="mtg", password="pw")
        guild = GuildFactory(
            meeting_cadence=Guild.MeetingCadence.MONTHLY,
            meeting_weekday=3,
            meeting_week_of_month=3,
            meeting_location="Studio B",
        )
        resp = client.get(reverse("hub_guild_detail", args=[guild.pk]))
        assert b"Next Meeting" in resp.content
        assert b"Studio B" in resp.content

    def it_shows_tba_when_marked_tba(client: Client):
        _editor("mtg2")
        client.login(username="mtg2", password="pw")
        guild = GuildFactory(meeting_is_tba=True)
        resp = client.get(reverse("hub_guild_detail", args=[guild.pk]))
        assert b"Next Meeting" in resp.content
        assert b"TBA" in resp.content
