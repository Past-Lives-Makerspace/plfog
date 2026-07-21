"""BDD specs for the Community Calendar 'expand a busy day' interaction.

Clicking the "+N" overflow indicator on a day cell expands it in place to reveal
every event for that day, then flips to "Show less" to collapse again. The count
limit differs per view (2 in the compact month grid, 3 in the week grid), so both
paths are covered. This is pure template/Alpine markup, so the assertions pin the
rendered expand control rather than any server-side context.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.template.loader import render_to_string
from django.utils import timezone

from membership.models import CalendarEvent
from tests.membership.factories import GuildFactory

pytestmark = pytest.mark.django_db


def _make_events(guild, count: int) -> list[CalendarEvent]:
    """Create ``count`` real (pk-bearing) events tied to ``guild``."""
    now = timezone.now()
    events = []
    for i in range(count):
        start = now + timedelta(days=1, hours=i)
        events.append(
            CalendarEvent.objects.create(
                guild=guild,
                uid=f"expand-{guild.pk}-{i}",
                title=f"Busy Day Event {i}",
                start_dt=start,
                end_dt=start + timedelta(hours=1),
                fetched_at=now,
            )
        )
    return events


def _day(events: list[CalendarEvent]) -> dict:
    return {"date": timezone.now().date(), "is_today": False, "in_month": True, "events": events}


def _render(*, week_days=None, month_days=None) -> str:
    ctx = {
        "week_offset": 0,
        "month_offset": 0,
        "event_page": 1,
        "event_total_pages": 1,
        "month_event_pages_json": "{}",
        "events_url": "/calendar/events/",
        "week_days": week_days or [],
        "month_days": month_days or [],
        "month_headers": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "week_events": [],
        "month_events": [],
        "source_colors": {},
    }
    return render_to_string("hub/partials/calendar_content.html", ctx)


def describe_calendar_day_expand():
    def describe_month_grid():
        def it_renders_a_clickable_toggle_when_a_day_has_more_than_two_events():
            guild = GuildFactory()
            events = _make_events(guild, 3)
            html = _render(month_days=[_day(events)])

            assert "pl-calendar-grid__more--toggle" in html
            assert '@click="expanded = !expanded"' in html
            # Collapse affordance: the same control flips to "Show less".
            assert "expanded ? 'Show less'" in html

        def it_renders_all_events_and_gates_the_overflow_behind_expanded():
            guild = GuildFactory()
            events = _make_events(guild, 3)
            html = _render(month_days=[_day(events)])

            # Every event is now in the DOM (no longer sliced away)...
            for event in events:
                assert f"focusEvent({event.pk})" in html
            # ...but the 3rd (overflow) chip only shows once expanded.
            assert "&& expanded" in html

        def it_gives_each_day_cell_its_own_expand_state():
            guild = GuildFactory()
            html = _render(month_days=[_day(_make_events(guild, 3))])

            assert 'x-data="{ expanded: false }"' in html
            assert "'pl-calendar-grid__day--expanded': expanded" in html

        def it_shows_no_toggle_when_two_or_fewer_events():
            guild = GuildFactory()
            html = _render(month_days=[_day(_make_events(guild, 2))])

            assert "pl-calendar-grid__more--toggle" not in html
            # Nothing overflows, so no chip is gated behind expansion.
            assert "&& expanded" not in html

    def describe_week_grid():
        def it_renders_a_clickable_toggle_when_a_day_has_more_than_three_events():
            guild = GuildFactory()
            events = _make_events(guild, 4)
            html = _render(week_days=[_day(events)])

            assert "pl-calendar-grid__more--toggle" in html
            assert "expanded ? 'Show less'" in html
            for event in events:
                assert f"focusEvent({event.pk})" in html
            assert "&& expanded" in html

        def it_shows_no_toggle_when_three_or_fewer_events():
            guild = GuildFactory()
            html = _render(week_days=[_day(_make_events(guild, 3))])

            assert "pl-calendar-grid__more--toggle" not in html
            assert "&& expanded" not in html
