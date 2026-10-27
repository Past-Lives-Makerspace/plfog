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

    def it_shows_studio_hours_with_their_title_when_they_come_first(client: Client):
        # Anything the guild staff coordinates is the Next Meeting (#953), titled when it is an event.
        _member(client, "v5")
        guild = GuildFactory()
        start = timezone.localtime(timezone.now()) + timezone.timedelta(days=2)
        CommunityEventFactory(
            studio_hours=True,
            guild=guild,
            title="Open Bench Hours",
            starts_at=start,
            ends_at=start + timezone.timedelta(hours=3),
        )
        content = client.get(f"/guilds/{guild.slug}/").content.decode()
        card = content.split('<h3 class="hub-detail-label">Next Meeting</h3>')[1].split("Get Involved")[0]
        assert '<div class="hub-member-name pl-guild-next__title">Open Bench Hours</div>' in card
        assert f'<div class="pl-guild-next__date">{date_filter(start, "l, F j")}</div>' in card
        assert f"Next up {date_filter(start, 'M j')}" in content
        assert "Next meeting " not in content.split('class="pl-guild-statbar__chips"')[1].split("</div>")[0]

    def it_says_next_meeting_in_the_badge_for_a_meeting(client: Client):
        _member(client, "v7")
        guild = GuildFactory()
        start = timezone.localtime(timezone.now()) + timezone.timedelta(days=2)
        CommunityEventFactory(
            guild_meeting=True, guild=guild, starts_at=start, ends_at=start + timezone.timedelta(hours=1)
        )
        content = client.get(f"/guilds/{guild.slug}/").content.decode()
        assert f"Next meeting {date_filter(start, 'M j')}" in content

    def it_still_reads_tba_for_a_tba_guild_with_nothing_dated(client: Client):
        _member(client, "v6")
        guild = GuildFactory(meeting_is_tba=True)
        content = client.get(f"/guilds/{guild.slug}/").content.decode()
        card = content.split('<h3 class="hub-detail-label">Next Meeting</h3>')[1].split("Get Involved")[0]
        assert "TBA" in card
        assert "Next meeting " not in content.split('class="pl-guild-statbar__chips"')[1].split("</div>")[0]


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
