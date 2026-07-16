"""BDD specs for the studio-hours additions to CommunityEvent + Guild: the widened
guild-matches-type constraint, announce/reminder suppression for STUDIO_HOURS, the
studio_hours()/meetings() queryset filters, and the Guild read helpers that drive the
guild page (studio_hours_display + next_meeting_occurrence).

All datetime math is asserted in Portland local time."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from membership.events import event_happening_now_occurrences, event_reminder_occurrences
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory


def _aware(y: int, m: int, d: int, hour: int = 12, minute: int = 0) -> datetime:
    return timezone.make_aware(datetime(y, m, d, hour, minute))


def describe_studio_hours_constraint():
    def it_requires_a_guild_for_a_studio_hours_row(db):
        with pytest.raises(IntegrityError), transaction.atomic():
            CommunityEvent.objects.create(
                title="Homeless hours",
                event_type=CommunityEvent.EventType.STUDIO_HOURS,
                guild=None,
                recurrence=CommunityEvent.Recurrence.WEEKLY,
                starts_at=_aware(2026, 7, 7, 18),
                ends_at=_aware(2026, 7, 7, 21),
            )

    def it_saves_a_studio_hours_row_with_a_guild(db):
        guild = GuildFactory()
        event = CommunityEvent.objects.create(
            title=f"{guild.name} Studio Hours",
            event_type=CommunityEvent.EventType.STUDIO_HOURS,
            guild=guild,
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=_aware(2026, 7, 7, 18),
            ends_at=_aware(2026, 7, 7, 21),
        )
        assert event.pk is not None

    def it_still_rejects_a_guild_on_a_community_event(db):
        guild = GuildFactory()
        with pytest.raises(IntegrityError), transaction.atomic():
            CommunityEvent.objects.create(
                title="Community with guild",
                event_type=CommunityEvent.EventType.COMMUNITY,
                guild=guild,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )


def describe_import_source_uid_uniqueness():
    def it_allows_many_blank_uids_but_rejects_a_duplicate_nonblank_uid(db):
        # The partial unique constraint excludes the "" default so app-authored rows repeat freely.
        CommunityEventFactory(import_source_uid="")
        CommunityEventFactory(import_source_uid="")
        CommunityEventFactory(import_source_uid="uid-1@google.com")
        with pytest.raises(IntegrityError), transaction.atomic():
            CommunityEventFactory(import_source_uid="uid-1@google.com")


def describe_announce_suppression():
    def it_does_not_announce_a_studio_hours_row(db):
        event = CommunityEventFactory(studio_hours=True)
        with patch("core.events.emit.emit") as mock_emit:
            event.announce()
        mock_emit.assert_not_called()

    def it_still_announces_a_guild_meeting(db):
        event = CommunityEventFactory(guild_meeting=True)
        with patch("core.events.emit.emit") as mock_emit:
            event.announce()
        assert mock_emit.call_args.args[0] == "event.guild_published"


def describe_reminder_suppression():
    def it_yields_no_reminder_or_happening_now_occurrences_for_studio_hours(db):
        now = timezone.now()
        # A studio-hours row as the editor creates it: reminder/notify toggles stay off.
        CommunityEventFactory(
            studio_hours=True,
            starts_at=now + timedelta(days=2),
            ends_at=now + timedelta(days=2, hours=3),
        )
        assert list(event_reminder_occurrences(now)) == []
        assert list(event_happening_now_occurrences(now)) == []


def describe_queryset_filters():
    def it_separates_studio_hours_from_meetings(db):
        guild = GuildFactory()
        hours = CommunityEventFactory(studio_hours=True, guild=guild)
        meeting = CommunityEventFactory(guild_meeting=True, guild=guild)
        assert list(CommunityEvent.objects.studio_hours()) == [hours]
        assert list(CommunityEvent.objects.meetings()) == [meeting]


def describe_studio_hours_display():
    def it_returns_an_empty_list_when_the_guild_has_none(db):
        guild = GuildFactory()
        assert guild.studio_hours_display() == []

    def it_orders_blocks_by_weekday_then_time_with_labels(db):
        guild = GuildFactory()
        # Saturday 10:00–14:00 and Tuesday 18:00–21:00 — Tuesday must sort first.
        CommunityEventFactory(
            studio_hours=True,
            guild=guild,
            starts_at=_aware(2026, 7, 11, 10),  # Saturday
            ends_at=_aware(2026, 7, 11, 14),
            location="Studio B",
            description="Bring your own clay.",
        )
        CommunityEventFactory(
            studio_hours=True,
            guild=guild,
            starts_at=_aware(2026, 7, 7, 18),  # Tuesday
            ends_at=_aware(2026, 7, 7, 21),
        )
        blocks = guild.studio_hours_display()
        assert [b["weekday_label"] for b in blocks] == ["Tuesdays", "Saturdays"]
        assert blocks[0]["time_range"] == "6:00–9:00 PM"
        assert blocks[1]["time_range"] == "10:00 AM–2:00 PM"
        assert blocks[1]["location"] == "Studio B"
        assert blocks[1]["note"] == "Bring your own clay."


def describe_next_meeting_occurrence():
    def it_returns_none_when_there_is_no_meeting_or_cadence(db):
        guild = GuildFactory()
        assert guild.next_meeting_occurrence() is None

    def it_ignores_studio_hours_rows(db):
        guild = GuildFactory()
        CommunityEventFactory(
            studio_hours=True, guild=guild, starts_at=_aware(2026, 7, 7, 18), ends_at=_aware(2026, 7, 7, 21)
        )
        assert guild.next_meeting_occurrence() is None

    def it_falls_back_to_the_legacy_cadence_when_no_event_meeting(db):
        today = timezone.localdate()
        guild = GuildFactory(
            meeting_next_override=today + timedelta(days=10),
            meeting_time=time(18, 0),
            meeting_location="Studio B",
        )
        result = guild.next_meeting_occurrence()
        assert result is not None
        assert timezone.localdate(result.when) == today + timedelta(days=10)
        assert result.location == "Studio B"
        assert result.has_time is True

    def it_prefers_a_sooner_event_meeting_over_the_legacy_cadence(db):
        now = timezone.now()
        today = timezone.localdate()
        guild = GuildFactory(
            meeting_next_override=today + timedelta(days=30),
            meeting_time=time(18, 0),
            meeting_location="Studio B",
        )
        meeting = CommunityEventFactory(
            guild_meeting=True,
            guild=guild,
            starts_at=now + timedelta(days=3),
            ends_at=now + timedelta(days=3, hours=2),
            location="Main hall",
        )
        result = guild.next_meeting_occurrence()
        assert result is not None
        assert result.when == meeting.starts_at
        assert result.location == "Main hall"
