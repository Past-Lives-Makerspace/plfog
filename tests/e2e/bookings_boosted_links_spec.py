"""End-to-end: boosted links inside the bookings refresh wrappers bring the whole page (#768).

Manage Bookings' Needs Attention body and the two Bookings tabs' pane bodies each carry the
refund refresh's ``hx-select`` and ``hx-swap``. The body is boosted, and htmx lets a
descendant inherit both, so without ``hx-disinherit`` a boosted link inside them selects the
wrapper from the next page: nothing, for Manage Bookings' "View all" or a member's name into
another page (an empty body), or the pane alone for a chip (no header, tabs or sidebar). A
guild lead follows each and lands with the page around it, and the arrival is still a boosted
swap: a stamp on ``window`` before the click is there afterwards, which a full document load
would lose (``hx-disinherit="*"`` cuts ``hx-boost`` off too). GET only; run with
``pytest -m e2e`` on PostgreSQL (``tests/e2e/README.md``).
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import EquipmentReservation, OrientationBooking
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
)

LEAD_EMAIL = "view-all-lead@example.com"
STAMP = "() => { window.__plBoostStamp = true; }"
STAMPED = "() => Boolean(window.__plBoostStamp)"


def _lead_with_a_request_waiting(login_via_code) -> OrientationBooking:
    """Sign a guild lead in; seed one request waiting on their guild, two days out."""
    MembershipPlanFactory()
    login_via_code(LEAD_EMAIL)
    lead = get_user_model().objects.get(username=LEAD_EMAIL).member
    guild = GuildFactory(name="View All Woodshop", guild_lead=lead)
    starts = timezone.now() + timedelta(days=2)
    slot = OrientationSlotFactory(guild=guild, starts_at=starts, ends_at=starts + timedelta(hours=1))
    return OrientationBookingFactory(
        slot=slot,
        member=MemberFactory(full_legal_name="Rune Viewallperson"),
        status=OrientationBooking.Status.REQUESTED,
    )


def _reservation_awaiting(booking: OrientationBooking) -> EquipmentReservation:
    """Seed a laser cutter the booking's guild lead manages, with one reservation waiting on their approval."""
    laser = EquipmentFactory(name="View All Laser", requires_approval=True)
    EquipmentStaffMembershipFactory(equipment=laser, member=booking.slot.guild.guild_lead)
    return EquipmentReservationFactory(
        equipment=laser,
        member=MemberFactory(full_legal_name="Sol Viewallperson"),
        status=EquipmentReservation.Status.PENDING_APPROVAL,
        purpose="Cutting a sign",
    )


def _boosted_click(page, selector: str) -> None:
    """Click the way a member does, with a stamp on ``window`` that only a boosted swap keeps."""
    page.evaluate(STAMP)
    page.locator(selector).click()


def _expect_the_page_around(page, heading: str) -> None:
    """The arrival is a whole hub page (its header and the sidebar) and the document survived the click."""
    expect(page.get_by_role("heading", name=heading, level=1)).to_be_visible()
    expect(page.locator(".hub-sidebar")).to_be_visible()
    assert page.evaluate(STAMPED), "the arrival was a full document load, not a boosted swap"


def describe_manage_bookings():
    def it_opens_the_needs_a_reply_chip_with_the_page_around_it(live_server, page, login_via_code):
        booking = _lead_with_a_request_waiting(login_via_code)
        page.goto(f"{live_server.url}{reverse('hub_manage_bookings')}")
        expect(page.locator(f'[data-booking-row="{booking.pk}"]')).to_be_visible()

        _boosted_click(page, '[data-view-all="orientations"]')

        expect(page).to_have_url(f"{live_server.url}{reverse('hub_orientations')}?view=bookings&show=reply")
        expect(page.locator('[data-bookings-chip="reply"]')).to_have_attribute("aria-current", "true")
        expect(page.locator(f'[data-booking-row="{booking.pk}"]')).to_be_visible()
        expect(page.get_by_role("tab", name="Bookings")).to_be_visible()
        _expect_the_page_around(page, "Orientations")

    def it_opens_the_needs_approval_chip_with_the_page_around_it(live_server, page, login_via_code):
        booking = _lead_with_a_request_waiting(login_via_code)
        reservation = _reservation_awaiting(booking)
        page.goto(f"{live_server.url}{reverse('hub_manage_bookings')}")
        expect(page.locator(f'[data-reservation-row="{reservation.pk}"]')).to_be_visible()

        _boosted_click(page, '[data-view-all="reservations"]')

        expect(page).to_have_url(f"{live_server.url}{reverse('hub_equipment_index')}?view=bookings&show=needs_approval")
        expect(page.locator('[data-bookings-chip="needs_approval"]')).to_have_attribute("aria-current", "true")
        expect(page.locator(f'[data-reservation-row="{reservation.pk}"]')).to_be_visible()
        expect(page.get_by_role("tab", name="Bookings")).to_be_visible()
        _expect_the_page_around(page, "Reservations")

    def it_opens_a_request_from_the_members_name_with_the_page_around_it(live_server, page, login_via_code):
        booking = _lead_with_a_request_waiting(login_via_code)
        page.goto(f"{live_server.url}{reverse('hub_manage_bookings')}")
        respond = reverse("hub_orientation_respond", args=[booking.pk])
        # The row menu's View Request shares the href; the name in the Member cell is the one clicked.
        name = f'[data-booking-row="{booking.pk}"] td[data-label="Member"] a[href="{respond}"]'
        expect(page.locator(name)).to_have_text("Rune Viewallperson")

        _boosted_click(page, name)

        expect(page).to_have_url(f"{live_server.url}{respond}")
        _expect_the_page_around(page, "Orientation Request")


def describe_the_bookings_tabs_chips():
    def it_keeps_the_orientations_page_around_the_all_chip(live_server, page, login_via_code):
        booking = _lead_with_a_request_waiting(login_via_code)
        page.goto(f"{live_server.url}{reverse('hub_orientations')}?view=bookings")
        expect(page.locator('[data-bookings-pane="staff"]')).to_be_visible()

        _boosted_click(page, '[data-bookings-chip="all"]')

        expect(page.locator('[data-bookings-chip="all"]')).to_have_attribute("aria-current", "true")
        expect(page.locator(f'[data-booking-row="{booking.pk}"]')).to_be_visible()
        expect(page.get_by_role("tab", name="Bookings")).to_be_visible()
        _expect_the_page_around(page, "Orientations")

    def it_keeps_the_reservations_page_around_the_all_chip(live_server, page, login_via_code):
        _lead_with_a_request_waiting(login_via_code)
        page.goto(f"{live_server.url}{reverse('hub_equipment_index')}?view=bookings")
        expect(page.locator("[data-bookings-pane]")).to_be_visible()

        _boosted_click(page, '[data-bookings-chip="all"]')

        expect(page.locator('[data-bookings-chip="all"]')).to_have_attribute("aria-current", "true")
        expect(page.get_by_role("tab", name="Bookings")).to_be_visible()
        _expect_the_page_around(page, "Reservations")
