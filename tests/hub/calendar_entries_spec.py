"""BDD specs for the synthetic calendar-entry wrapper."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from hub.calendar_entries import CalendarEntry


def _entry(**kwargs) -> CalendarEntry:
    now = timezone.now()
    base = {"pk": 1, "title": "x", "start_dt": now, "end_dt": now + timedelta(hours=1), "source": "classes"}
    base.update(kwargs)
    return CalendarEntry(**base)


def describe_CalendarEntry():
    def it_exposes_source_as_source_key():
        assert _entry(source="orientation").source_key == "orientation"

    def it_is_in_progress_between_start_and_end():
        now = timezone.now()
        assert _entry(start_dt=now - timedelta(hours=1), end_dt=now + timedelta(hours=1)).is_in_progress is True

    def it_is_not_in_progress_before_it_starts():
        now = timezone.now()
        assert _entry(start_dt=now + timedelta(hours=1), end_dt=now + timedelta(hours=2)).is_in_progress is False

    def it_is_never_in_progress_for_all_day_entries():
        now = timezone.now()
        entry = _entry(start_dt=now - timedelta(hours=1), end_dt=now + timedelta(hours=1), all_day=True)
        assert entry.is_in_progress is False
