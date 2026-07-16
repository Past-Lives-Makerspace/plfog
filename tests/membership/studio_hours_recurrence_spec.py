"""BDD specs for the WEEKLY recurrence branch on CommunityEvent — occurrences_in windows,
edges, DST, and the FREQ=WEEKLY;BYDAY iCal rule. Monthly outputs are regression-checked so
the new branch never disturbs the existing month-anchored machinery.

All datetime math is asserted in Portland local time (the app's TIME_ZONE)."""

from __future__ import annotations

from datetime import date, datetime

from django.utils import timezone

from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory


def _aware(y: int, m: int, d: int, hour: int = 12, minute: int = 0) -> datetime:
    """A timezone-aware datetime at ``hour:minute`` local time."""
    return timezone.make_aware(datetime(y, m, d, hour, minute))


def describe_weekly_recurrence():
    def describe_occurrences_in():
        def it_yields_every_seven_days_within_the_window(db):
            # 2026-07-07 is a Tuesday.
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.WEEKLY,
                starts_at=_aware(2026, 7, 7, 18),
                ends_at=_aware(2026, 7, 7, 21),
            )
            occ = event.occurrences_in(date(2026, 7, 1), date(2026, 7, 31))
            days = [timezone.localtime(o).day for o in occ]
            assert days == [7, 14, 21, 28]
            for o in occ:
                assert timezone.localtime(o).weekday() == 1  # Tuesday

        def it_preserves_the_anchor_time_of_day(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.WEEKLY,
                starts_at=_aware(2026, 7, 7, 18, 30),
                ends_at=_aware(2026, 7, 7, 21),
            )
            occ = event.occurrences_in(date(2026, 7, 8), date(2026, 7, 31))
            for o in occ:
                local = timezone.localtime(o)
                assert (local.hour, local.minute) == (18, 30)

        def it_never_yields_before_the_anchor_date(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.WEEKLY,
                starts_at=_aware(2026, 7, 21, 18),  # a Tuesday mid-month
                ends_at=_aware(2026, 7, 21, 21),
            )
            # Window opens before the anchor — earlier Tuesdays must not appear.
            occ = event.occurrences_in(date(2026, 7, 1), date(2026, 7, 31))
            days = [timezone.localtime(o).day for o in occ]
            assert days == [21, 28]

        def it_respects_both_window_edges_inclusively(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.WEEKLY,
                starts_at=_aware(2026, 7, 7, 18),
                ends_at=_aware(2026, 7, 7, 21),
            )
            # Both edges land exactly on an occurrence date.
            occ = event.occurrences_in(date(2026, 7, 14), date(2026, 7, 28))
            days = [timezone.localtime(o).day for o in occ]
            assert days == [14, 21, 28]

        def it_returns_empty_when_the_window_precedes_the_anchor(db):
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.WEEKLY,
                starts_at=_aware(2026, 7, 21, 18),
                ends_at=_aware(2026, 7, 21, 21),
            )
            assert event.occurrences_in(date(2026, 6, 1), date(2026, 7, 20)) == []

        def it_keeps_wall_clock_time_across_a_dst_boundary(db):
            # US DST ends 2026-11-01. A Tuesday-anchored weekly series spanning it must keep
            # 18:00 local on both sides of the fall-back, not drift by an hour.
            event = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.WEEKLY,
                starts_at=_aware(2026, 10, 27, 18),  # Tuesday before DST ends
                ends_at=_aware(2026, 10, 27, 21),
            )
            occ = event.occurrences_in(date(2026, 10, 27), date(2026, 11, 10))
            locals_ = [timezone.localtime(o) for o in occ]
            assert [(d.month, d.day) for d in locals_] == [(10, 27), (11, 3), (11, 10)]
            assert all(d.hour == 18 for d in locals_)  # wall-clock preserved across the DST fall-back

    def describe_ical_rrule():
        def it_emits_freq_weekly_byday_for_each_weekday(db):
            # 2026-07-06 Mon … 2026-07-12 Sun — one per weekday.
            expected = {
                6: "MO",
                7: "TU",
                8: "WE",
                9: "TH",
                10: "FR",
                11: "SA",
                12: "SU",
            }
            for day, code in expected.items():
                event = CommunityEventFactory(
                    recurrence=CommunityEvent.Recurrence.WEEKLY,
                    starts_at=_aware(2026, 7, day, 18),
                    ends_at=_aware(2026, 7, day, 21),
                )
                assert event.ical_rrule() == f"FREQ=WEEKLY;BYDAY={code}"

        def it_leaves_monthly_and_yearly_rules_unchanged(db):
            monthly = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.MONTHLY,
                starts_at=_aware(2026, 7, 11, 18),  # 2nd Saturday
                ends_at=_aware(2026, 7, 11, 20),
            )
            assert monthly.ical_rrule() == "FREQ=MONTHLY;BYDAY=2SA"
            yearly = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.YEARLY,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )
            assert yearly.ical_rrule() == "FREQ=YEARLY;BYMONTH=7;BYDAY=2SA"
            semi = CommunityEventFactory(
                recurrence=CommunityEvent.Recurrence.SEMI_MONTHLY,
                starts_at=_aware(2026, 7, 11, 18),
                ends_at=_aware(2026, 7, 11, 20),
            )
            assert semi.ical_rrule() == "FREQ=MONTHLY;BYDAY=2SA,4SA"
