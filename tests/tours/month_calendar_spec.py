"""The booking page's Pick a Day as a month calendar (Morlock, 2026-11-22): a block per month, seven
columns Sunday to Saturday, every date under its weekday, the days without a tour greyed."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest
from django.core.cache import cache
from django.test import Client
from django.utils import timezone

from membership.models import Member
from tests.tours.factories import TourHoursFactory
from tours.picker import DayChoice, calendar_months

pytestmark = pytest.mark.django_db

HOST = "tours.pastlives.space"


def _days(*days: date, picked: date | None = None) -> list[DayChoice]:
    return [DayChoice(day=day, selected=day == picked) for day in days]


def describe_calendar_months():
    def it_is_empty_without_days():
        assert calendar_months([]) == []

    def it_starts_every_week_on_sunday():
        # Mon Nov 23 2026: its week runs Sun Nov 22 to Sat Nov 28.
        (november,) = calendar_months(_days(date(2026, 11, 23)))
        week = next(week for week in november.weeks if any(cell.day == date(2026, 11, 23) for cell in week))
        assert [cell.day for cell in week] == [date(2026, 11, 22) + timedelta(days=offset) for offset in range(7)]
        assert all(len(week) == 7 for week in november.weeks)

    def it_makes_a_block_per_month_from_the_first_tour_to_the_last():
        months = calendar_months(_days(date(2026, 11, 23), date(2026, 12, 22)))
        assert [month.first for month in months] == [date(2026, 11, 1), date(2026, 12, 1)]

    def it_leaves_other_months_days_blank():
        (november,) = calendar_months(_days(date(2026, 11, 23)))
        first_week, last_week = november.weeks[0], november.weeks[-1]
        # November 2026 starts on a Sunday and ends on a Monday.
        assert first_week[0].day == date(2026, 11, 1)
        assert [cell.day for cell in last_week] == [
            date(2026, 11, 29),
            date(2026, 11, 30),
            None,
            None,
            None,
            None,
            None,
        ]

    def it_links_only_the_days_with_a_tour():
        (november,) = calendar_months(_days(date(2026, 11, 23), date(2026, 11, 25), picked=date(2026, 11, 25)))
        cells = {cell.day: cell for week in november.weeks for cell in week if cell.day}
        assert cells[date(2026, 11, 23)].choice == DayChoice(day=date(2026, 11, 23), selected=False)
        assert cells[date(2026, 11, 25)].choice == DayChoice(day=date(2026, 11, 25), selected=True)
        assert cells[date(2026, 11, 24)].choice is None


def describe_the_booking_page():
    @pytest.fixture(autouse=True)
    def _fresh_rate_limits() -> None:
        cache.clear()

    def it_draws_months_with_weekday_columns(guide: Member):
        soon = timezone.localdate() + timedelta(days=4)
        TourHoursFactory(guide=guide, weekday=soon.weekday(), seats=4)
        content = Client(HTTP_HOST=HOST).get("/").content.decode()
        assert f'data-tour-month="{soon:%Y-%m}"' in content
        assert f"{soon:%B %Y}</h3>" in content
        header = content[content.index("data-tour-weekdays") :]
        assert [header.index(name) for name in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")] == sorted(
            header.index(name) for name in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")
        )
        # The day with a tour is a link; the day after (no hours that weekday) is a greyed cell.
        assert f'data-tour-day="{soon:%Y-%m-%d}"' in content
        after = soon + timedelta(days=1)
        assert f'data-tour-closed="{after:%Y-%m-%d}"' in content
        assert f'data-tour-day="{after:%Y-%m-%d}"' not in content

    def it_keeps_the_kiosks_arrow_group(guide: Any):
        soon = timezone.localdate() + timedelta(days=4)
        TourHoursFactory(guide=guide, weekday=soon.weekday(), seats=4)
        content = Client(HTTP_HOST=HOST).get("/").content.decode()
        # static/js/tours-kiosk.js walks the links inside .pl-tourpub__days with the arrow keys.
        assert 'class="pl-tourpub__days pl-tourpub__calendar"' in content
