"""Every calendar draws an event on its local (Portland) day, not its UTC one.

``start_dt`` is stored in UTC, so bucketing by its own date put every event from 5 PM
Pacific (4 PM in winter) in the next day's cell, and flipped "today" at 5 PM. The clock is
frozen at 17:30 Pacific on day D, when the UTC date is already D + 1, and each event starts
at 18:00 Pacific on D, which is D + 1 in UTC too. Time is frozen the repo's way, by
patching ``django.utils.timezone.now``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from hub.calendar_entries import (
    ORIENTATION_PK_OFFSET,
    RESERVATION_PK_OFFSET,
    CalendarEntry,
    calendar_day,
)
from membership.models import CalendarEvent
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

# D is the Wednesday two to eight days out: mid week, so the day before it is in the same
# Week grid, and near enough that the session made at the real time is still valid.
_SOON = timezone.localdate() + timedelta(days=2)
DAY = _SOON + timedelta(days=(2 - _SOON.weekday()) % 7)
# In UTC, as the real timezone.now() returns it.
FROZEN_NOW = timezone.make_aware(datetime.combine(DAY, time(17, 30))).astimezone(UTC)
EVENING = timezone.make_aware(datetime.combine(DAY, time(18, 0)))


@pytest.fixture
def frozen() -> Iterator[None]:
    with patch("django.utils.timezone.now", return_value=FROZEN_NOW):
        yield


def _login(client: Client, username: str) -> None:
    MembershipPlanFactory()
    User.objects.create_user(username=username, password="pass")
    client.login(username=username, password="pass")


def _cell_pks(days: list[dict[str, Any]], day: date) -> list[int]:
    [cell] = [cell for cell in days if cell["date"] == day]
    return [event.pk for event in cell["events"]]


def _assert_on_day_d(context: Any, pk: int) -> None:
    """``pk`` sits in D's cell in the Week grid and the Month grid, and in no other cell."""
    for grid in ("week_days", "month_days"):
        assert pk in _cell_pks(context[grid], DAY), grid
        assert pk not in _cell_pks(context[grid], DAY + timedelta(days=1)), grid
    assert pk in [event.pk for event in context["week_events"]]


def describe_the_test_clock():
    def it_is_already_tomorrow_in_utc():
        assert FROZEN_NOW.astimezone(UTC).date() == DAY + timedelta(days=1)
        assert EVENING.astimezone(UTC).date() == DAY + timedelta(days=1)


def describe_calendar_day():
    def _entry(start: datetime, *, all_day: bool = False) -> CalendarEntry:
        return CalendarEntry(pk=1, title="x", start_dt=start, end_dt=start, source="classes", all_day=all_day)

    def it_reads_an_evening_event_on_its_local_day():
        assert calendar_day(_entry(EVENING)) == DAY

    def it_keeps_an_all_day_event_anchored_to_local_midnight_on_its_day():
        local_midnight = timezone.make_aware(datetime.combine(DAY, time.min))
        assert calendar_day(_entry(local_midnight, all_day=True)) == DAY

    def it_keeps_an_all_day_event_stored_at_utc_midnight_on_the_date_it_names():
        utc_midnight = datetime.combine(DAY, time.min, tzinfo=UTC)
        assert timezone.localtime(utc_midnight).date() == DAY - timedelta(days=1)
        assert calendar_day(_entry(utc_midnight, all_day=True)) == DAY

    def it_reads_a_timed_event_at_utc_midnight_on_its_local_day():
        utc_midnight = datetime.combine(DAY, time.min, tzinfo=UTC)
        assert calendar_day(_entry(utc_midnight)) == DAY - timedelta(days=1)


def describe_each_calendar_at_half_past_five_pacific():
    def it_marks_the_pacific_date_as_today(client: Client, frozen: None):
        _login(client, "ld_today")
        context = client.get(reverse("hub_community_calendar_events")).context
        [today] = [cell["date"] for cell in context["week_days"] if cell["is_today"]]
        assert today == DAY
        assert [cell["date"] for cell in context["month_days"] if cell["is_today"]] == [DAY]

    def it_draws_an_evening_guild_orientation_and_feed_event_on_their_day(client: Client, frozen: None):
        _login(client, "ld_guild")
        guild = GuildFactory(name="Evening Guild", calendar_url="https://example.com/evening.ics")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        slot = OrientationSlotFactory(guild=guild, starts_at=EVENING, ends_at=EVENING + timedelta(hours=1))
        feed_event = CalendarEvent.objects.create(
            guild=guild,
            source=CalendarEvent.Source.GUILD,
            uid="evening-feed",
            title="Evening Open Shop",
            start_dt=EVENING,
            end_dt=EVENING + timedelta(hours=2),
            fetched_at=FROZEN_NOW,
        )
        context = client.get(reverse("hub_guild_calendar_events", args=[guild.pk])).context
        _assert_on_day_d(context, ORIENTATION_PK_OFFSET + slot.pk)
        _assert_on_day_d(context, feed_event.pk)

    def it_draws_an_evening_event_on_its_day_on_the_community_calendar(client: Client, frozen: None):
        _login(client, "ld_community")
        feed_event = CalendarEvent.objects.create(
            source=CalendarEvent.Source.GENERAL,
            uid="evening-general",
            title="Evening Potluck",
            start_dt=EVENING,
            end_dt=EVENING + timedelta(hours=2),
            fetched_at=FROZEN_NOW,
        )
        _assert_on_day_d(client.get(reverse("hub_community_calendar_events")).context, feed_event.pk)

    def it_draws_an_evening_slot_on_its_day_on_the_orientations_calendar(client: Client, frozen: None):
        _login(client, "ld_orientations")
        guild = GuildFactory(name="Evening Orientations Guild")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        slot = OrientationSlotFactory(
            guild=guild,
            orientation_type=OrientationTypeFactory(guild=guild),
            starts_at=EVENING,
            ends_at=EVENING + timedelta(hours=1),
        )
        context = client.get(reverse("hub_orientations_calendar_events")).context
        _assert_on_day_d(context, ORIENTATION_PK_OFFSET + slot.pk)

    def it_draws_an_evening_reservation_on_its_day_on_the_reservations_calendar(client: Client, frozen: None):
        _login(client, "ld_reservations")
        reservation = EquipmentReservationFactory(
            equipment=EquipmentFactory(name="Evening saw"), starts_at=EVENING, ends_at=EVENING + timedelta(hours=1)
        )
        context = client.get(reverse("hub_equipment_calendar_events")).context
        _assert_on_day_d(context, RESERVATION_PK_OFFSET + reservation.pk)

    def it_renders_the_evening_chip_inside_day_ds_cell(client: Client, frozen: None):
        _login(client, "ld_markup")
        reservation = EquipmentReservationFactory(
            equipment=EquipmentFactory(name="Markup saw"), starts_at=EVENING, ends_at=EVENING + timedelta(hours=1)
        )
        content = client.get(reverse("hub_equipment_calendar_events")).content.decode()
        week = content[content.index("pl-calendar-grid--week") : content.index("pl-calendar-grid--month")]
        cells = re.split(r'<div class="pl-calendar-grid__day[ "]', week)[1:]
        pk = f"focusEvent({RESERVATION_PK_OFFSET + reservation.pk})"
        [cell] = [cell for cell in cells if pk in cell]
        assert f'pl-calendar-grid__day-num pl-calendar-grid__day-num--today">{DAY.day}<' in cell

    def it_keeps_an_all_day_event_on_its_date(client: Client, frozen: None):
        _login(client, "ld_all_day")
        anchored = CalendarEvent.objects.create(
            source=CalendarEvent.Source.GENERAL,
            uid="all-day-local",
            title="Local Midnight Fair",
            start_dt=timezone.make_aware(datetime.combine(DAY, time.min)),
            end_dt=timezone.make_aware(datetime.combine(DAY + timedelta(days=1), time.min)),
            all_day=True,
            fetched_at=FROZEN_NOW,
        )
        utc_midnight = CalendarEvent.objects.create(
            source=CalendarEvent.Source.GENERAL,
            uid="all-day-utc",
            title="UTC Midnight Fair",
            start_dt=datetime.combine(DAY, time.min, tzinfo=UTC),
            end_dt=datetime.combine(DAY + timedelta(days=1), time.min, tzinfo=UTC),
            all_day=True,
            fetched_at=FROZEN_NOW,
        )
        context = client.get(reverse("hub_community_calendar_events")).context
        for event in (anchored, utc_midnight):
            for grid in ("week_days", "month_days"):
                assert event.pk in _cell_pks(context[grid], DAY), (event.uid, grid)
                assert event.pk not in _cell_pks(context[grid], DAY - timedelta(days=1)), (event.uid, grid)
