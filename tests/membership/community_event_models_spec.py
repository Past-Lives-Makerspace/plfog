"""BDD specs for the CommunityEvent model: constraints, queryset, monthly recurrence,
display properties, and the one-shot announce()."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory


def _aware(y: int, m: int, d: int, hour: int = 12) -> datetime:
    """A timezone-aware datetime at ``hour`` local time (noon by default, so the local
    date never rolls over relative to UTC)."""
    return timezone.make_aware(datetime(y, m, d, hour, 0))


def describe_CommunityEvent():
    def describe_constraints():
        def it_rejects_end_equal_to_or_before_start(db):
            guild = GuildFactory()
            with pytest.raises(IntegrityError), transaction.atomic():
                CommunityEvent.objects.create(
                    title="Bad",
                    event_type=CommunityEvent.EventType.GUILD_MEETING,
                    guild=guild,
                    starts_at=_aware(2026, 7, 11),
                    ends_at=_aware(2026, 7, 11),
                )

        def it_allows_end_after_start(db):
            guild = GuildFactory()
            event = CommunityEvent.objects.create(
                title="Good",
                event_type=CommunityEvent.EventType.GUILD_MEETING,
                guild=guild,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )
            assert event.pk is not None

        def it_requires_a_guild_for_a_guild_meeting(db):
            with pytest.raises(IntegrityError), transaction.atomic():
                CommunityEvent.objects.create(
                    title="No guild",
                    event_type=CommunityEvent.EventType.GUILD_MEETING,
                    guild=None,
                    starts_at=_aware(2026, 7, 11, 18),
                    ends_at=_aware(2026, 7, 11, 20),
                )

        def it_rejects_a_guild_on_a_community_event(db):
            guild = GuildFactory()
            with pytest.raises(IntegrityError), transaction.atomic():
                CommunityEvent.objects.create(
                    title="Community with guild",
                    event_type=CommunityEvent.EventType.COMMUNITY,
                    guild=guild,
                    starts_at=_aware(2026, 7, 11, 18),
                    ends_at=_aware(2026, 7, 11, 20),
                )

        def it_rejects_a_guild_on_a_lead_meeting(db):
            guild = GuildFactory()
            with pytest.raises(IntegrityError), transaction.atomic():
                CommunityEvent.objects.create(
                    title="Lead meeting with guild",
                    event_type=CommunityEvent.EventType.LEAD_MEETING,
                    guild=guild,
                    starts_at=_aware(2026, 7, 11, 18),
                    ends_at=_aware(2026, 7, 11, 20),
                )

        def it_allows_the_three_valid_combos(db):
            guild = GuildFactory()
            CommunityEventFactory(guild_meeting=True, guild=guild)
            CommunityEventFactory(community=True)
            CommunityEventFactory(lead_meeting=True)
            assert CommunityEvent.objects.count() == 3

    def describe_meta_and_str():
        def it_orders_by_starts_at_ascending(db):
            late = CommunityEventFactory(starts_at=_aware(2026, 9, 1, 18), ends_at=_aware(2026, 9, 1, 20))
            early = CommunityEventFactory(starts_at=_aware(2026, 7, 1, 18), ends_at=_aware(2026, 7, 1, 20))
            assert list(CommunityEvent.objects.all()) == [early, late]

        def it_shows_the_guild_name_for_a_guild_event(db):
            guild = GuildFactory(name="Metal Guild")
            event = CommunityEventFactory(guild=guild, title="Forge Night")
            assert "Metal Guild" in str(event)
            assert "Forge Night" in str(event)

        def it_shows_site_wide_for_a_community_event(db):
            event = CommunityEventFactory(community=True, title="Potluck")
            assert "Site-wide" in str(event)

    def describe_queryset():
        def it_upcoming_excludes_a_past_nonrecurring_event(db):
            now = timezone.now()
            CommunityEventFactory(starts_at=now - timedelta(days=3), ends_at=now - timedelta(days=2))
            assert not CommunityEvent.objects.upcoming().exists()

        def it_upcoming_includes_a_future_nonrecurring_event(db):
            now = timezone.now()
            event = CommunityEventFactory(starts_at=now + timedelta(days=2), ends_at=now + timedelta(days=2, hours=2))
            assert list(CommunityEvent.objects.upcoming()) == [event]

        def it_upcoming_includes_a_past_anchored_monthly_series(db):
            now = timezone.now()
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.MONTHLY,
                starts_at=now - timedelta(days=400),
                ends_at=now - timedelta(days=400) + timedelta(hours=2),
            )
            assert list(CommunityEvent.objects.upcoming()) == [event]

        def it_candidates_for_window_includes_a_past_anchored_monthly(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.MONTHLY,
                starts_at=_aware(2020, 1, 11, 18),
                ends_at=_aware(2020, 1, 11, 20),
            )
            window = CommunityEvent.objects.candidates_for_window(date(2026, 7, 1), date(2026, 7, 31))
            assert list(window) == [event]

        def it_candidates_for_window_excludes_a_nonrecurring_event_outside(db):
            CommunityEventFactory(starts_at=_aware(2026, 1, 11, 18), ends_at=_aware(2026, 1, 11, 20))
            window = CommunityEvent.objects.candidates_for_window(date(2026, 7, 1), date(2026, 7, 31))
            assert not window.exists()

        def it_for_guild_and_site_wide_partition(db):
            guild = GuildFactory()
            mine = CommunityEventFactory(guild=guild)
            CommunityEventFactory(community=True)
            assert list(CommunityEvent.objects.for_guild(guild)) == [mine]
            assert list(CommunityEvent.objects.site_wide().filter(event_type="community"))

    def describe_recurrence():
        def it_occurrence_ordinal_returns_2_for_a_2nd_saturday(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
            assert event._occurrence_ordinal() == 2

        def it_occurrence_ordinal_returns_minus_1_for_a_5th_weekday(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 5, 30, 18), ends_at=_aware(2026, 5, 30, 20))
            assert event._occurrence_ordinal() == -1

        def it_occurrences_in_returns_the_start_for_a_nonrecurring_in_window(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
            occ = event.occurrences_in(date(2026, 7, 1), date(2026, 7, 31))
            assert occ == [event.starts_at]

        def it_occurrences_in_returns_empty_out_of_window(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
            assert event.occurrences_in(date(2026, 8, 1), date(2026, 8, 31)) == []

        def it_occurrences_in_expands_monthly_same_nth_weekday(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.MONTHLY,
                starts_at=_aware(2026, 7, 11, 18),  # 2nd Saturday of July
                ends_at=_aware(2026, 7, 11, 20),
            )
            occ = event.occurrences_in(date(2026, 8, 1), date(2026, 10, 31))
            assert len(occ) == 3
            for d in occ:
                local = timezone.localtime(d)
                assert local.weekday() == 5  # Saturday
                assert (local.day - 1) // 7 + 1 == 2  # the 2nd Saturday
                assert local.hour == 18  # time-of-day preserved
            assert [timezone.localtime(d).month for d in occ] == [8, 9, 10]

    def describe_display():
        def it_absolute_url_is_prefixed_with_member_base_url(db, settings):
            settings.MEMBER_BASE_URL = "https://members.test"
            event = CommunityEventFactory()
            assert event.absolute_url.startswith("https://members.test")

        def it_when_display_shows_a_range_without_repeats_for_nonrecurring(db):
            event = CommunityEventFactory(starts_at=_aware(2026, 7, 11, 18), ends_at=_aware(2026, 7, 11, 20))
            text = event.when_display
            assert "–" in text
            assert "Repeats monthly" not in text

        def it_when_display_appends_repeats_monthly(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.MONTHLY,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )
            assert "Repeats monthly" in event.when_display

    def describe_announce():
        def it_picks_guild_published_for_a_guild_event(db):
            guild = GuildFactory()
            event = CommunityEventFactory(guild=guild)
            with patch("core.events.emit.emit") as mock_emit:
                event.announce()
            assert mock_emit.call_args.args[0] == "event.guild_published"

        def it_picks_community_published_for_a_community_event(db):
            event = CommunityEventFactory(community=True)
            with patch("core.events.emit.emit") as mock_emit:
                event.announce()
            assert mock_emit.call_args.args[0] == "event.community_published"

        def it_picks_lead_meeting_published_for_a_lead_meeting(db):
            event = CommunityEventFactory(lead_meeting=True)
            with patch("core.events.emit.emit") as mock_emit:
                event.announce()
            assert mock_emit.call_args.args[0] == "event.lead_meeting_published"

        def it_passes_the_guild_in_context_and_an_absolute_url(db, settings):
            settings.MEMBER_BASE_URL = "https://members.test"
            guild = GuildFactory()
            event = CommunityEventFactory(guild=guild)
            with patch("core.events.emit.emit") as mock_emit:
                event.announce()
            kwargs = mock_emit.call_args.kwargs
            assert kwargs["context"]["guild"] == guild
            assert kwargs["url"].startswith("https://members.test")
            assert kwargs["period"] == f"event:{event.pk}:published"

        def it_carries_no_guild_for_a_site_wide_event(db):
            event = CommunityEventFactory(community=True)
            with patch("core.events.emit.emit") as mock_emit:
                event.announce()
            assert mock_emit.call_args.kwargs["context"]["guild"] is None

        def it_is_idempotent_via_period(db):
            from core.models import EventDelivery

            event = CommunityEventFactory(community=True)
            event.announce()
            after_first = EventDelivery.objects.filter(period=f"event:{event.pk}:published").count()
            event.announce()
            after_second = EventDelivery.objects.filter(period=f"event:{event.pk}:published").count()
            assert after_first >= 1
            assert after_first == after_second
