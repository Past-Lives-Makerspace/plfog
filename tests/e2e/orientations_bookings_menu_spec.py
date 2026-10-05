"""End-to-end: the Orientations page's Bookings tab (#626) and its "..." row menu in a real browser.

A guild lead opens the tab from List (the pane loads only then), picks the All chip, opens a
row's menu, moves through it with the keyboard and closes it with Escape, then marks the
member oriented through the confirm modal and lands back on the tab with the chip kept and
the Oriented pill showing. At 375px the table stacks, the menu still opens, and the page has
no sideways scroll. Waits are on what the page shows, never a snapshot after it (the boosted
arrival trap). Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import OrientationBooking
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

LEAD_EMAIL = "bookings-menu-lead@example.com"
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


def _lead_with_a_confirmed_booking(login_via_code) -> OrientationBooking:
    """Sign a guild lead in; seed one confirmed booking on their guild, two days out."""
    MembershipPlanFactory()
    login_via_code(LEAD_EMAIL)
    lead = get_user_model().objects.get(username=LEAD_EMAIL).member
    guild = GuildFactory(name="Menu Woodshop", guild_lead=lead)
    starts = timezone.now() + timedelta(days=2)
    slot = OrientationSlotFactory(guild=guild, starts_at=starts, ends_at=starts + timedelta(hours=1))
    return OrientationBookingFactory(
        slot=slot,
        member=MemberFactory(full_legal_name="Kai Menuperson"),
        status=OrientationBooking.Status.CONFIRMED,
    )


def describe_the_bookings_tab_menu():
    def it_marks_a_member_oriented_from_the_menu_and_lands_back_on_the_tab(live_server, page, login_via_code):
        booking = _lead_with_a_confirmed_booking(login_via_code)
        page.goto(f"{live_server.url}{reverse('hub_orientations')}")

        # The List view has no Bookings pane until the tab is opened.
        expect(page.locator("[data-bookings-pane]")).to_have_count(0)
        page.get_by_role("tab", name="Bookings").click()
        expect(page.locator('[data-bookings-pane="staff"]')).to_be_visible()
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_orientations')}?view=bookings")

        page.locator('[data-bookings-chip="all"]').click()
        expect(page.locator('[data-bookings-chip="all"]')).to_have_attribute("aria-current", "true")
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_orientations')}?view=bookings&show=all")

        row = page.locator(f'[data-booking-row="{booking.pk}"]')
        trigger = row.get_by_role("button", name="Actions for Kai Menuperson")
        trigger.click()
        menu = row.get_by_role("menu")
        expect(menu).to_be_visible()
        expect(menu.get_by_role("menuitem", name="View Request")).to_be_focused()
        page.keyboard.press("ArrowDown")
        expect(menu.get_by_role("menuitem", name="Email Member")).to_be_focused()
        page.keyboard.press("Escape")
        expect(menu).to_be_hidden()
        expect(trigger).to_be_focused()

        trigger.click()
        menu.get_by_role("menuitem", name="Mark Oriented").click()
        dialog = page.get_by_role("dialog").filter(has_text="Mark Kai Menuperson as Oriented?")
        expect(dialog).to_be_visible()
        dialog.get_by_role("button", name="Mark Oriented").click()

        expect(page.locator(f'[data-booking-row="{booking.pk}"] [data-booking-oriented]')).to_be_visible()
        expect(page.locator('[data-bookings-chip="all"]')).to_have_attribute("aria-current", "true")
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_orientations')}?view=bookings&show=all")
        booking.refresh_from_db()
        assert booking.is_completed is True

    def it_stacks_on_a_phone_with_the_menu_in_reach_and_no_sideways_scroll(live_server, page, login_via_code):
        page.set_viewport_size({"width": 375, "height": 812})
        booking = _lead_with_a_confirmed_booking(login_via_code)
        page.goto(f"{live_server.url}{reverse('hub_orientations')}?view=bookings")

        row = page.locator(f'[data-booking-row="{booking.pk}"]')
        expect(row).to_be_visible()
        # Stacked: the table header is gone and the cells carry their own labels.
        expect(page.locator(".pl-bookings-table thead").first).to_be_hidden()
        page.wait_for_function(NO_SIDEWAYS_SCROLL)

        # The filters fold into a Filters disclosure; search stays out.
        expect(page.locator(".pl-bookings-filters__more > summary")).to_be_visible()
        expect(page.locator('input[name="search"]')).to_be_visible()

        trigger = row.get_by_role("button", name="Actions for Kai Menuperson")
        expect(trigger).to_be_in_viewport()
        trigger.click()
        menu = row.get_by_role("menu")
        expect(menu).to_be_visible()
        expect(menu).to_be_in_viewport()
        page.wait_for_function(NO_SIDEWAYS_SCROLL)
