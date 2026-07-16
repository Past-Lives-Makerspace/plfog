"""BDD specs for how studio hours surface to members: the guild-page "Studio Hours" card
(lines, empty state, the exact tooltip copy), the weekly meeting flowing into "Next Meeting",
and the home "Upcoming" widget labelling studio hours as "Studio hours".

Asserts on markup (role="tooltip", the computed date), never on incidental visible copy that
the what's-new widget might echo. Datetime math is Portland local time."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import User
from django.template.defaultfilters import date as date_filter
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from membership.models import CommunityEvent
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    GuildMembershipFactory,
    MembershipPlanFactory,
)

pytestmark = pytest.mark.django_db


def _aware(y: int, m: int, d: int, hour: int, minute: int = 0):
    return timezone.make_aware(timezone.datetime(y, m, d, hour, minute))


def _member(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    client.login(username=username, password="pass")
    return user


def describe_guild_page_studio_hours_card():
    def it_renders_a_line_per_studio_hours_block(client: Client):
        _member(client, "v1")
        guild = GuildFactory()
        CommunityEventFactory(
            studio_hours=True,
            guild=guild,
            starts_at=_aware(2026, 7, 7, 18),  # Tuesday
            ends_at=_aware(2026, 7, 7, 21),
            location="Studio B",
        )
        content = client.get(f"/guilds/{guild.slug}/").content.decode()
        assert "Studio Hours" in content
        assert "Tuesdays · 6:00–9:00 PM · Studio B" in content

    def it_shows_the_empty_state_when_no_hours_are_set(client: Client):
        _member(client, "v2")
        guild = GuildFactory()
        content = client.get(f"/guilds/{guild.slug}/").content.decode()
        assert "No studio hours set yet." in content

    def it_carries_the_exact_tooltip_copy(client: Client):
        _member(client, "v3")
        guild = GuildFactory()
        content = client.get(f"/guilds/{guild.slug}/").content.decode()
        assert 'role="tooltip"' in content
        assert "Come chat with the Guild Lead during these times." in content


def describe_next_meeting_card():
    def it_surfaces_a_weekly_meeting_entered_as_an_event(client: Client):
        _member(client, "v4")
        guild = GuildFactory()
        # A weekly meeting anchored to an upcoming Tuesday shows in "Next Meeting".
        anchor = timezone.localtime(timezone.now()) + timezone.timedelta(days=3)
        CommunityEventFactory(
            guild_meeting=True,
            guild=guild,
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=anchor,
            ends_at=anchor + timezone.timedelta(hours=2),
        )
        content = client.get(f"/guilds/{guild.slug}/").content.decode()
        next_meeting = guild.next_meeting_occurrence()
        assert next_meeting is not None
        assert date_filter(next_meeting.when, "l, F j") in content


def describe_home_upcoming_widget():
    def it_labels_studio_hours_rows_as_studio_hours(client: Client):
        user = _member(client, "home_sh")
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=user.member)
        CommunityEventFactory(studio_hours=True, guild=guild)
        upcoming = client.get(reverse("hub_home")).context["upcoming"]
        kinds = {item.kind for item in upcoming}
        assert "Studio hours" in kinds
        assert "Meeting" not in kinds  # the studio-hours row is not mislabelled as a meeting
