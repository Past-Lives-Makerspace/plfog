"""BDD specs for the Reservations page's Calendar view (#502 part 3).

What is taken across every active item: confirmed reservations and booked orientations on an
item's own types, one legend chip per item. Assertions anchor on markup and factory names
(STANDARDS.md, Testing Traps).
"""

from __future__ import annotations

import re
from datetime import timedelta
from unittest import mock

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from hub.calendar_entries import ORIENTATION_ITEM_STRIDE, ORIENTATION_PK_OFFSET, RESERVATION_PK_OFFSET
from hub.calendar_pages import ITEM_LEGEND_COLORS, reservations_calendar_context
from membership.models import EquipmentReservation, OrientationBooking
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

pytestmark = pytest.mark.django_db

PAGE = "/equipment/"
EVENTS = "/equipment/calendar/events/"


def _login(client: Client, username: str) -> User:
    MembershipPlanFactory()
    user = User.objects.create_user(username=username, password="pass")
    client.login(username=username, password="pass")
    return user


def _tomorrow(hour: int = 10) -> object:
    """Tomorrow at ``hour`` local time: the same UTC date, inside the current window."""
    return timezone.localtime().replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=1)


def _reserve(item: object, **kwargs: object) -> EquipmentReservation:
    starts_at = kwargs.pop("starts_at", _tomorrow())
    return EquipmentReservationFactory(
        equipment=item, starts_at=starts_at, ends_at=starts_at + timedelta(hours=2), **kwargs
    )


def _legend(content: bytes) -> list[str]:
    text = content.decode()
    row = text[text.index('class="pl-calendar-filters"') : text.index('id="pl-calendar-events-area"')]
    return re.findall(r">\s*([^<>]+?)\s*</button>", row)


def _chip_titles(content: bytes) -> list[str]:
    return re.findall(r'<span class="pl-calendar-grid__chip-title">([^<]*)</span>', content.decode())


def describe_the_tabs():
    def it_opens_on_the_list_with_the_calendar_left_to_load_later(client: Client):
        _login(client, "rc_tabs")
        content = client.get(PAGE).content
        assert b"plListCalendar('list')" in content
        assert b'aria-label="Reservations views"' in content
        assert f'data-lazy-pane="calendar" data-pane-src="{EVENTS}?shell=1"'.encode() in content
        assert b'id="pl-calendar-events-area"' not in content

    def it_builds_no_calendar_for_the_list_view(client: Client):
        _login(client, "rc_no_queries")
        EquipmentFactory()
        with mock.patch("hub.equipment_views.reservations_calendar_context") as build:
            assert client.get(PAGE).status_code == 200
        build.assert_not_called()

    def it_opens_on_the_calendar_for_view_calendar(client: Client):
        _login(client, "rc_open")
        content = client.get(f"{PAGE}?view=calendar").content
        assert b"plListCalendar('calendar')" in content
        assert b'id="pl-calendar-events-area"' in content
        assert b"guildCalFiltersOff-reservations" in content


def describe_the_legend():
    def it_has_one_chip_per_active_item_in_name_order(client: Client):
        _login(client, "rc_legend")
        EquipmentFactory(name="Lathe", guild=GuildFactory(name="Woodworking Guild"))
        laser = EquipmentFactory(name="Laser cutter")
        EquipmentFactory(name="Retired saw", is_active=False)
        content = client.get(f"{PAGE}?view=calendar").content
        assert _legend(content) == ["Laser cutter", "Lathe"]
        assert f"isActive('{laser.pk}')".encode() in content
        assert f"--filter-color: {ITEM_LEGEND_COLORS[0]};".encode() in content
        assert f"--filter-color: {ITEM_LEGEND_COLORS[1]};".encode() in content

    def it_repeats_the_palette_past_its_end():
        for n in range(len(ITEM_LEGEND_COLORS) + 1):
            EquipmentFactory(name=f"Item {n:02d}")
        legend = reservations_calendar_context()["legend"]
        assert legend[len(ITEM_LEGEND_COLORS)]["color"] == ITEM_LEGEND_COLORS[0]
        assert [chip["label"] for chip in legend] == sorted(chip["label"] for chip in legend)

    def it_ignores_the_pages_filter_chips(client: Client):
        _login(client, "rc_unfiltered")
        EquipmentFactory(name="Room A", kind="room")
        EquipmentFactory(name="Tool B")
        content = client.get(f"{PAGE}?view=calendar&kind=room&q=Room").content
        assert _legend(content) == ["Room A", "Tool B"]


def describe_the_events_partial():
    def it_requires_login(client: Client):
        response = client.get(EVENTS)
        assert response.status_code == 302
        assert "/accounts/login/" in response["Location"]

    def it_is_named_for_the_page(client: Client):
        assert reverse("hub_equipment_calendar_events") == EVENTS

    def it_shows_a_reservation_and_a_booked_orientation_under_their_item(client: Client):
        _login(client, "rc_partial")
        item = EquipmentFactory(name="Lathe", guild=GuildFactory(name="Woodworking Guild"))
        reservation = _reserve(item, member=MemberFactory(full_legal_name="Sam Reyes", preferred_name=""))
        slot = OrientationSlotFactory(
            guild=None,
            orientation_type=OrientationTypeFactory(guild=None, equipment=item),
            starts_at=_tomorrow(14),
            ends_at=_tomorrow(15),
        )
        OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)
        content = client.get(EVENTS).content
        assert set(_chip_titles(content)) == {"Lathe · Sam R.", "Lathe · Orientation"}
        assert f'data-event-pk="{RESERVATION_PK_OFFSET + reservation.pk}"'.encode() in content
        assert f'data-event-pk="{ORIENTATION_PK_OFFSET + slot.pk * ORIENTATION_ITEM_STRIDE}"'.encode() in content
        assert b"10:00 AM \xe2\x80\x93 12:00 PM" in content
        assert b">Lathe \xc2\xb7 Woodworking Guild</div>" in content
        assert f"isActive('{item.pk}')".encode() in content

    def it_links_the_items_page_on_that_day_in_the_same_tab(client: Client):
        _login(client, "rc_link")
        item = EquipmentFactory(name="Table saw")
        reservation = _reserve(item)
        content = client.get(EVENTS).content.decode()
        url = f"/equipment/{item.slug}/?day={timezone.localdate(reservation.starts_at).isoformat()}"
        link = re.search(rf'<a href="{re.escape(url)}"[^>]*>', content)
        assert link is not None
        assert "target=" not in link.group(0)
        assert ">Table saw · Makerspace</div>" in content

    def it_leaves_out_a_cancelled_reservation_and_an_open_slot(client: Client):
        _login(client, "rc_absent")
        item = EquipmentFactory(name="Quiet item")
        _reserve(item, status=EquipmentReservation.Status.CANCELLED)
        OrientationSlotFactory(
            guild=None, orientation_type=OrientationTypeFactory(guild=None, equipment=item), starts_at=_tomorrow()
        )
        content = client.get(EVENTS).content
        assert _chip_titles(content) == []

    def it_leaves_out_an_inactive_items_reservations(client: Client):
        _login(client, "rc_inactive")
        _reserve(EquipmentFactory(name="Retired", is_active=False))
        assert _chip_titles(client.get(EVENTS).content) == []

    def it_drops_the_guild_calendars_feed_notes(client: Client):
        _login(client, "rc_notes")
        content = client.get(EVENTS).content
        assert b"pl-calendar-list-note" not in content
        assert b"pl-calendar-empty__hint" not in content

    def it_returns_the_whole_shell_for_the_first_load(client: Client):
        _login(client, "rc_shell")
        EquipmentFactory(name="Shell item")
        content = client.get(f"{EVENTS}?shell=1").content
        assert b"guildCalFiltersOff-reservations" in content
        assert _legend(content) == ["Shell item"]


def describe_query_counts():
    def _seed(names: range) -> None:
        for n in names:
            item = EquipmentFactory(name=f"Count item {n}")
            _reserve(item)
            slot = OrientationSlotFactory(
                guild=None,
                orientation_type=OrientationTypeFactory(guild=None, equipment=item),
                starts_at=_tomorrow(16),
                ends_at=_tomorrow(17),
            )
            OrientationBookingFactory(slot=slot, status=OrientationBooking.Status.CONFIRMED)

    def it_builds_the_calendar_in_four_queries_for_one_item_or_six(django_assert_num_queries):
        _seed(range(1))
        with django_assert_num_queries(4):
            assert len(reservations_calendar_context()["month_events"]) == 2
        _seed(range(1, 6))
        with django_assert_num_queries(4):
            cal = reservations_calendar_context()
        assert len(cal["legend"]) == 6
        assert cal["event_total_pages"] == 2

    def it_answers_the_partial_in_the_same_queries_for_one_item_or_six(client: Client, django_assert_num_queries):
        _login(client, "rc_count")
        _seed(range(1))
        client.get(EVENTS)  # warm the per process caches
        with CaptureQueriesContext(connection) as one:
            client.get(EVENTS)
        _seed(range(1, 6))
        with django_assert_num_queries(len(one.captured_queries)):
            content = client.get(EVENTS).content
        assert len(set(_chip_titles(content))) == 12  # a reservation and an orientation on each of six items


def describe_the_items_page_day():
    def it_opens_the_schedule_on_the_linked_day_with_the_strip_paged_to_it(client: Client):
        _login(client, "rc_day")
        item = EquipmentFactory(name="Day item", max_advance_days=60)
        day = timezone.localdate() + timedelta(days=10)
        EquipmentHoursFactory(equipment=item, weekday=day.weekday())
        response = client.get(f"/equipment/{item.slug}/?day={day.isoformat()}")
        assert response.context["selected_day"] == day
        assert response.context["week_offset"] == 1

    def it_keeps_todays_week_for_no_day_or_a_past_one(client: Client):
        _login(client, "rc_no_day")
        item = EquipmentFactory(name="No day item")
        assert client.get(f"/equipment/{item.slug}/").context["week_offset"] == 0
        past = timezone.localdate() - timedelta(days=9)
        assert client.get(f"/equipment/{item.slug}/?day={past.isoformat()}").context["week_offset"] == 0
