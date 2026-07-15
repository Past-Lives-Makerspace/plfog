"""BDD specs for the synthetic calendar-entry wrapper."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from hub.calendar_entries import CalendarEntry
from tests.membership.factories import GuildFactory


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


@pytest.mark.django_db
def describe_CalendarEntry_source_key():
    def it_keys_a_class_entry_by_its_guild():
        guild = GuildFactory()
        assert _entry(source="classes", guild=guild).source_key == str(guild.pk)

    def it_falls_back_to_classes_for_a_class_entry_with_no_guild():
        assert _entry(source="classes", guild=None).source_key == "classes"

    def it_keeps_orientation_its_own_color_even_with_a_guild():
        # Orientation stays amber — the guild must NOT recolor it.
        guild = GuildFactory()
        assert _entry(source="orientation", guild=guild).source_key == "orientation"

    def it_keeps_community_its_own_color_even_with_a_guild():
        # Community stays blue — the guild must NOT recolor it.
        guild = GuildFactory()
        assert _entry(source="community", guild=guild).source_key == "community"


def _guild_series(day_offsets: list[int]) -> object:
    """A guild with a published series whose sessions fall at the given day offsets."""
    guild = GuildFactory()
    category = CategoryFactory(guild=guild)
    offering = ClassOfferingFactory(
        status=ClassOffering.Status.PUBLISHED,
        category=category,
        scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE,
    )
    for days in day_offsets:
        start = timezone.now() + timedelta(days=days)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return guild


@pytest.mark.django_db
def describe_guild_calendar_entries():
    def _window() -> tuple[object, object]:
        today = timezone.now().date()
        return today - timedelta(days=10), today + timedelta(days=30)

    def it_omits_a_started_series():
        # A guild's own calendar must drop a started series just like the catalog.
        from hub.calendar_entries import guild_calendar_entries

        guild = _guild_series([-3, 4, 11])
        fetch_from, fetch_to = _window()

        entries = guild_calendar_entries(guild, fetch_from, fetch_to)

        assert [e for e in entries if e.source == "classes"] == []

    def it_keeps_a_future_series():
        from hub.calendar_entries import guild_calendar_entries

        guild = _guild_series([2, 9, 16])
        fetch_from, fetch_to = _window()

        entries = guild_calendar_entries(guild, fetch_from, fetch_to)

        class_entries = [e for e in entries if e.source == "classes"]
        assert len(class_entries) == 3
