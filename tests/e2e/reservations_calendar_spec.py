"""End-to-end: the Reservations page's Calendar view (#502 part 3) in a real browser.

A member opens the Calendar, finds a reservation's chip under its item, follows the entry
and lands on the item's page with its schedule open on that day. At phone width the legend
of eight items wraps instead of pushing the page sideways. Waits are on what the page
shows. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from hub.calendar_entries import RESERVATION_PK_OFFSET
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentReservationFactory,
    MemberFactory,
    MembershipPlanFactory,
)

MEMBER_EMAIL = "reservations-calendar-member@example.com"
PHONE = {"width": 390, "height": 844}
NO_H_SCROLL = "() => document.documentElement.scrollWidth === document.documentElement.clientWidth"
CALENDAR_READY = "() => !!(document.querySelector('.pl-calendar-page') || {})._x_dataStack"


def describe_the_reservations_calendar():
    def it_finds_a_reservation_and_lands_on_the_items_day(live_server, page, login_via_code):
        MembershipPlanFactory()
        item = EquipmentFactory(name="Table saw")
        EquipmentFactory(name="Kiln")
        starts_at = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
        EquipmentHoursFactory(equipment=item, weekday=starts_at.weekday())
        reservation = EquipmentReservationFactory(
            equipment=item,
            member=MemberFactory(full_legal_name="Sam Reyes", preferred_name=""),
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
        )

        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_equipment_index')}")
        page.get_by_role("tab", name="Calendar").click()
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_equipment_index')}?view=calendar")
        expect(page.locator(".pl-calendar-filter")).to_have_text(["Kiln", "Table saw"])
        page.wait_for_function(CALENDAR_READY)

        page.get_by_role("button", name="Month", exact=True).click()
        chip = page.locator(".pl-calendar-grid--month .pl-calendar-grid__chip", has_text="Table saw · Sam R.")
        expect(chip).to_be_visible()
        chip.click()

        item_row = page.locator(
            f'.pl-calendar-list__item[data-event-pk="{RESERVATION_PK_OFFSET + reservation.pk}"]:visible'
        )
        item_row.locator("a.pl-calendar-list__title--link").click()

        day = starts_at.date()
        expect(page).to_have_url(
            f"{live_server.url}{reverse('hub_equipment_detail', args=[item.slug])}?day={day.isoformat()}"
        )
        expect(page.locator("#equipment-schedule h3.hub-detail-label")).to_have_text(day.strftime("%A, %B %-d"))

    def it_wraps_a_legend_of_eight_items_at_phone_width(live_server, page, login_via_code):
        MembershipPlanFactory()
        for name in (
            "Bandsaw",
            "Classroom A",
            "Kiln",
            "Laser cutter",
            "Lathe",
            "Loading dock",
            "Photo studio",
            "Table saw",
        ):
            EquipmentFactory(name=name)

        page.set_viewport_size(PHONE)
        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_equipment_index')}?view=calendar")
        chips = page.locator(".pl-calendar-filter")
        expect(chips).to_have_count(8)
        first, last = chips.first.bounding_box(), chips.last.bounding_box()
        assert first is not None and last is not None
        assert last["y"] > first["y"], "the legend did not wrap onto a second row"
        assert last["x"] + last["width"] <= PHONE["width"]
        assert page.evaluate(NO_H_SCROLL), "the Reservations calendar scrolls sideways at 390px"
