"""BDD specs for the seed_guild_calendar_events command against FIXTURE iCal bytes (no network).

Covers: creating matched+classified rows, skipping+reporting ambiguous/no-match/unclassified,
idempotency (a second run creates nothing — create-if-absent), --dry-run writing nothing, and
the --ical-url/--feed argument guards."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from membership.models import CommunityEvent
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db

_FIXTURE = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Test//EN
BEGIN:VEVENT
UID:ceramics-hours@google.com
SUMMARY:Ceramics Guild Studio Hours
DTSTART:20260707T180000Z
DTEND:20260707T210000Z
LOCATION:Studio B
RRULE:FREQ=WEEKLY;BYDAY=TU
END:VEVENT
BEGIN:VEVENT
UID:fig-meeting@google.com
SUMMARY:FIG Guild Meeting
DTSTART:20260710T180000Z
DTEND:20260710T193000Z
RRULE:FREQ=MONTHLY;BYDAY=2FR
END:VEVENT
BEGIN:VEVENT
UID:joint@google.com
SUMMARY:Ceramics Guild & Glass Guild Joint Meeting
DTSTART:20260712T140000Z
DTEND:20260712T160000Z
END:VEVENT
BEGIN:VEVENT
UID:potluck@google.com
SUMMARY:Random Potluck
DTSTART:20260712T140000Z
DTEND:20260712T160000Z
END:VEVENT
BEGIN:VEVENT
UID:happy@google.com
SUMMARY:Glass Guild Happy Hour
DTSTART:20260713T170000Z
DTEND:20260713T190000Z
END:VEVENT
END:VCALENDAR
"""


def _seed_guilds() -> None:
    GuildFactory(name="Ceramics Guild")
    GuildFactory(name="Food Independence Guild")
    GuildFactory(name="Glass Guild")


def _run(**kwargs: str) -> str:
    out = StringIO()
    with patch("membership.management.commands.seed_guild_calendar_events._fetch_ical_bytes", return_value=_FIXTURE):
        call_command("seed_guild_calendar_events", stdout=out, **kwargs)
    return out.getvalue()


def describe_seed_guild_calendar_events():
    def it_creates_the_matched_and_classified_rows(db):
        _seed_guilds()
        _run(ical_url="https://example.test/cal.ics")
        hours = CommunityEvent.objects.get(import_source_uid="ceramics-hours@google.com")
        assert hours.event_type == CommunityEvent.EventType.STUDIO_HOURS
        assert hours.recurrence == CommunityEvent.Recurrence.WEEKLY
        assert hours.google_calendar_target == CommunityEvent.GoogleCalendarTarget.PUBLIC
        assert hours.moderation_state == CommunityEvent.ModerationState.PUBLISHED
        assert hours.location == "Studio B"
        meeting = CommunityEvent.objects.get(import_source_uid="fig-meeting@google.com")
        assert meeting.event_type == CommunityEvent.EventType.GUILD_MEETING
        assert meeting.guild.name == "Food Independence Guild"

    def it_skips_and_reports_ambiguous_nomatch_and_unclassified(db):
        _seed_guilds()
        output = _run(ical_url="https://example.test/cal.ics")
        # Only the two clean rows are created.
        assert CommunityEvent.objects.count() == 2
        assert not CommunityEvent.objects.filter(import_source_uid="joint@google.com").exists()
        assert not CommunityEvent.objects.filter(import_source_uid="potluck@google.com").exists()
        assert not CommunityEvent.objects.filter(import_source_uid="happy@google.com").exists()
        assert "skip: ambiguous" in output
        assert "skip: no guild match" in output
        assert "skip: unclassified" in output
        assert "2 created, 3 skipped." in output

    def it_is_idempotent_on_a_second_run(db):
        _seed_guilds()
        _run(ical_url="https://example.test/cal.ics")
        first = CommunityEvent.objects.count()
        output = _run(ical_url="https://example.test/cal.ics")
        assert CommunityEvent.objects.count() == first  # nothing new
        assert "exists, skipped" in output
        assert "0 created, 5 skipped." in output

    def it_writes_nothing_on_a_dry_run(db):
        _seed_guilds()
        output = _run(ical_url="https://example.test/cal.ics", dry_run=True)
        assert CommunityEvent.objects.count() == 0
        assert "would create" in output
        assert "2 would create, 3 skipped." in output

    def it_requires_an_ical_url_or_feed(db):
        with pytest.raises(CommandError, match="Provide --ical-url"):
            call_command("seed_guild_calendar_events")

    def it_rejects_both_ical_url_and_feed(db):
        with pytest.raises(CommandError, match="only one of"):
            call_command("seed_guild_calendar_events", ical_url="https://x.test/c.ics", feed="General")

    def it_reads_a_named_calendar_feed(db):
        from core.models import CalendarFeed

        _seed_guilds()
        CalendarFeed.objects.create(name="Public", ical_url="https://example.test/public.ics")
        _run(feed="Public")
        assert CommunityEvent.objects.filter(import_source_uid="ceramics-hours@google.com").exists()

    def it_errors_on_an_unknown_feed_name(db):
        with pytest.raises(CommandError, match="No CalendarFeed named"):
            call_command("seed_guild_calendar_events", feed="Nope")
