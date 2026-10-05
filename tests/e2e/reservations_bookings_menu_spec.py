"""End-to-end: the Reservations page's Bookings tab (#627) and its "..." row menu in a real browser.

A manager opens the tab from List (the pane loads only then), opens a row's menu, picks Cancel
Reservation, finds the Confirm button disabled until a reason is typed, confirms, and lands back
on the tab with the row reading Cancelled by a manager. At 375px the table stacks, the menu still
opens in reach, and the page has no sideways scroll. Waits are on what the page shows, never a
snapshot after it (the boosted arrival trap). Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import EquipmentReservation
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
)

MANAGER_EMAIL = "reservations-menu-manager@example.com"
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


def _manager_with_a_reservation(login_via_code) -> EquipmentReservation:
    """Sign a laser cutter manager in; seed one confirmed reservation on it, two days out."""
    MembershipPlanFactory()
    login_via_code(MANAGER_EMAIL)
    manager = get_user_model().objects.get(username=MANAGER_EMAIL).member
    laser = EquipmentFactory(name="Laser Cutter")
    EquipmentStaffMembershipFactory(equipment=laser, member=manager)
    starts = timezone.now() + timedelta(days=2)
    return EquipmentReservationFactory(
        equipment=laser,
        member=MemberFactory(full_legal_name="Ana Menuperson"),
        starts_at=starts,
        ends_at=starts + timedelta(hours=2),
        purpose="Cutting signs",
    )


def describe_the_reservations_bookings_menu():
    def it_cancels_with_a_required_reason_and_lands_back_on_the_tab(live_server, page, login_via_code):
        reservation = _manager_with_a_reservation(login_via_code)
        index = f"{live_server.url}{reverse('hub_equipment_index')}"
        page.goto(index)

        expect(page.locator("[data-bookings-pane]")).to_have_count(0)
        page.get_by_role("tab", name="Bookings").click()
        expect(page.locator('[data-bookings-pane="staff"]')).to_be_visible()
        expect(page).to_have_url(f"{index}?view=bookings")

        row = page.locator(f'[data-reservation-row="{reservation.pk}"]')
        trigger = row.get_by_role("button", name="Actions for Ana Menuperson")
        trigger.click()
        menu = row.get_by_role("menu")
        expect(menu).to_be_visible()
        expect(menu.get_by_role("menuitem", name="View Equipment")).to_be_focused()
        page.keyboard.press("ArrowDown")
        expect(menu.get_by_role("menuitem", name="Manage Equipment")).to_be_focused()
        page.keyboard.press("Escape")
        expect(menu).to_be_hidden()
        expect(trigger).to_be_focused()

        trigger.click()
        menu.get_by_role("menuitem", name="Cancel Reservation").click()
        dialog = page.get_by_role("dialog").filter(has_text="Ana Menuperson has this time booked.")
        expect(dialog).to_be_visible()
        confirm = dialog.get_by_role("button", name="Cancel Reservation")
        expect(confirm).to_be_disabled()
        dialog.get_by_label("Reason").fill("The laser tube is being replaced.")
        expect(confirm).to_be_enabled()
        confirm.click()

        expect(page).to_have_url(f"{index}?view=bookings")
        # Upcoming no longer lists it; the Past chip shows it cancelled by a manager.
        expect(page.locator(f'[data-reservation-row="{reservation.pk}"]')).to_have_count(0)
        page.locator('[data-bookings-chip="past"]').click()
        expect(
            page.locator(f'[data-reservation-row="{reservation.pk}"] [data-reservation-status="cancelled-by-manager"]')
        ).to_be_visible()
        reservation.refresh_from_db()
        assert reservation.status == EquipmentReservation.Status.CANCELLED
        assert reservation.cancelled_reason == "The laser tube is being replaced."

    def it_stacks_on_a_phone_with_the_menu_in_reach_and_no_sideways_scroll(live_server, page, login_via_code):
        page.set_viewport_size({"width": 375, "height": 812})
        reservation = _manager_with_a_reservation(login_via_code)
        page.goto(f"{live_server.url}{reverse('hub_equipment_index')}?view=bookings")

        row = page.locator(f'[data-reservation-row="{reservation.pk}"]')
        expect(row).to_be_visible()
        expect(page.locator(".pl-bookings-table thead").first).to_be_hidden()
        page.wait_for_function(NO_SIDEWAYS_SCROLL)
        expect(page.locator(".pl-bookings-filters__more > summary")).to_be_visible()

        trigger = row.get_by_role("button", name="Actions for Ana Menuperson")
        expect(trigger).to_be_in_viewport()
        trigger.click()
        menu = row.get_by_role("menu")
        expect(menu).to_be_visible()
        expect(menu).to_be_in_viewport()
        page.wait_for_function(NO_SIDEWAYS_SCROLL)
