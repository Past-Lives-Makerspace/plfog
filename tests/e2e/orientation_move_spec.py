"""End-to-end: a leader moves an orientation to another time, and a member accepts a proposed one (#853).

A guild lead opens a row's "..." menu on the Bookings tab, picks Move to Another Time, fills
the modal's date, start and Move it now, and lands back on the tab with the row at the new
time and confirmed. From the request page the lead picks Ask the member first instead, and the
page shows the open proposal while the booking keeps its time. A member with an open proposal
taps Accept new time on their Orientations page card and the card shows the new, confirmed
time. Waits are on what the page shows, never a snapshot after it (the boosted arrival trap).
Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import dateformat, timezone
from playwright.sync_api import expect

from membership import orientations
from membership.models import OrientationBooking
from tests.membership.factories import (
    GuildFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

LEAD_EMAIL = "move-lead@example.com"
MEMBER_EMAIL = "move-member@example.com"


def _new_start() -> datetime:
    """Six days out at 2:00 PM local, on the modal's half hour grid."""
    day = timezone.localdate(timezone.now() + timedelta(days=6))
    return timezone.make_aware(datetime.combine(day, datetime.min.time().replace(hour=14)))


def _lead_with_a_request(login_via_code) -> OrientationBooking:
    """Sign a guild lead in; seed one requested booking on their guild, two days out."""
    MembershipPlanFactory()
    login_via_code(LEAD_EMAIL)
    lead = get_user_model().objects.get(username=LEAD_EMAIL).member
    guild = GuildFactory(name="Move Woodshop", guild_lead=lead)
    starts = timezone.now() + timedelta(days=2)
    slot = OrientationSlotFactory(guild=guild, starts_at=starts, ends_at=starts + timedelta(hours=1))
    return OrientationBookingFactory(slot=slot, member=MemberFactory(full_legal_name="Juno Moveperson"))


def _fill_move_modal(dialog, mode: str) -> None:
    start = _new_start()
    dialog.get_by_label("New date").fill(start.date().isoformat())
    dialog.get_by_label("New start time").select_option("14:00")
    dialog.get_by_label(mode).check()
    dialog.get_by_role("button", name="Move Orientation").click()


def describe_moving_an_orientation():
    def it_moves_a_request_now_from_the_bookings_tab_menu(live_server, page, login_via_code):
        booking = _lead_with_a_request(login_via_code)
        page.goto(f"{live_server.url}{reverse('hub_orientations')}?view=bookings")

        row = page.locator(f'[data-booking-row="{booking.pk}"]')
        row.get_by_role("button", name="Actions for Juno Moveperson").click()
        row.get_by_role("menu").get_by_role("menuitem", name="Move to Another Time").click()
        dialog = page.locator(f'[aria-labelledby="booking-move-{booking.pk}-title"]')
        expect(dialog).to_be_visible()
        expect(dialog.get_by_text("Nothing is refunded or charged.")).to_be_visible()
        _fill_move_modal(dialog, "Move it now")

        expected_when = dateformat.format(timezone.localtime(_new_start()), "D M j, g:i A")
        moved_row = page.locator(f'[data-booking-row="{booking.pk}"]')
        expect(moved_row.locator(".pl-bookings-table__when")).to_have_text(expected_when)
        expect(moved_row.locator('[data-booking-status="confirmed"]')).to_be_visible()
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_orientations')}?view=bookings")
        booking.refresh_from_db()
        assert booking.slot.starts_at == _new_start()
        assert booking.status == OrientationBooking.Status.CONFIRMED

    def it_asks_the_member_first_from_the_request_page(live_server, page, login_via_code):
        booking = _lead_with_a_request(login_via_code)
        old_slot = booking.slot_id
        page.goto(f"{live_server.url}{reverse('hub_orientation_respond', args=[booking.pk])}")

        page.locator("[data-orientation-move-open]").click()
        dialog = page.locator('[aria-labelledby="respond-move-title"]')
        expect(dialog).to_be_visible()
        _fill_move_modal(dialog, "Ask the member first")

        expect(page.locator("[data-booking-proposal]")).to_contain_text("You asked Juno Moveperson to move to")
        booking.refresh_from_db()
        assert booking.slot_id == old_slot
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.proposed_starts_at == _new_start()

    def it_lets_the_member_accept_on_their_card(live_server, page, login_via_code):
        MembershipPlanFactory()
        login_via_code(MEMBER_EMAIL)
        member = get_user_model().objects.get(username=MEMBER_EMAIL).member
        guild = GuildFactory(name="Accept Woodshop", guild_lead=MemberFactory())
        orientation_type = OrientationTypeFactory(guild=guild, name="Bandsaw Basics")
        slot = OrientationSlotFactory(guild=guild, orientation_type=orientation_type)
        booking = OrientationBookingFactory(slot=slot, member=member)
        orientations.propose_orientation_time(booking, _new_start(), actor=guild.guild_lead)
        page.goto(f"{live_server.url}{reverse('hub_orientations')}")

        proposal = page.locator(f'[data-orientation-proposal="{booking.pk}"]')
        expect(proposal).to_be_visible()
        proposal.get_by_role("button", name="Accept new time").click()

        card = page.locator(f'[id="orientation-type-{orientation_type.pk}"]')
        expect(card.locator(f'[data-orientation-proposal="{booking.pk}"]')).to_have_count(0)
        expected_day = dateformat.format(timezone.localtime(_new_start()), "l, F j")
        expect(card.locator(".pl-orient-card__booked")).to_contain_text(f"{expected_day} at 2:00 PM")
        expect(card.get_by_text("Confirmed", exact=True)).to_be_visible()
        booking.refresh_from_db()
        assert booking.moved_by_guild_at is not None
