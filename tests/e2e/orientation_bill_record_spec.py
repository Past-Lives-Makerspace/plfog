"""End-to-end: Send a bill in the Record Orientation window (#1111, part 1) in a real browser.

A guild lead opens Record Orientation. Picking a priced orientation fills Amount with its
price and ticks Send a bill, and the button reads Record and Send Bill; a donation one fills
its suggestion; a free one empties Amount and unticks it, and the button reads Record
Orientation again. Saving ticked lands on the Oriented list with the billed row and its
Unpaid chip. Waits are on what the page shows. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from typing import Any

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import Member, OrientationBooking, OrientationType
from tests.membership.factories import GuildFactory, MembershipPlanFactory, OrientationTypeFactory

LEAD_EMAIL = "bill-orientation-lead@example.com"
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


def _world(login_via_code: Any) -> dict[str, Any]:
    MembershipPlanFactory()
    login_via_code(LEAD_EMAIL)
    lead = get_user_model().objects.get(username=LEAD_EMAIL).member
    guild = GuildFactory(name="Billing Woodshop", guild_lead=lead)
    target = get_user_model().objects.create_user(username="billable-e2e", email="billable-e2e@example.com").member
    target.full_legal_name = "Bryn Billable"
    target.status = Member.Status.ACTIVE
    target.save(update_fields=["full_legal_name", "status"])
    return {
        "priced": OrientationTypeFactory(guild=guild, name="Lathe Basics", price_cents=4000),
        "donation": OrientationTypeFactory(
            guild=guild, name="Router Basics", is_donation=True, donation_suggested_cents=2500
        ),
        "free": OrientationTypeFactory(guild=guild, name="Shop Tour", price_cents=0),
        "target": target,
    }


def _open(page: Any, live_server: Any) -> Any:
    page.goto(f"{live_server.url}{reverse('hub_orientations')}?view=bookings")
    page.locator("[data-bookings-record]").click()
    dialog = page.get_by_role("dialog").filter(has_text="Record an Orientation")
    expect(dialog).to_be_visible()
    return dialog


def _pick(dialog: Any, orientation_type: OrientationType) -> None:
    dialog.locator("#orientation-record-orientation").fill(str(orientation_type))


def describe_send_a_bill():
    def it_prefills_by_price_kind_and_swaps_the_button(live_server, page, login_via_code):
        world = _world(login_via_code)
        dialog = _open(page, live_server)
        checkbox = dialog.locator("#orientation-record-send_bill")
        amount = dialog.locator("#orientation-record-bill_amount")
        submit = dialog.locator("[data-bookings-record-submit]")
        expect(submit).to_have_text("Record Orientation")
        expect(checkbox).not_to_be_checked()
        expect(amount).to_be_hidden()

        _pick(dialog, world["priced"])
        expect(checkbox).to_be_checked()
        expect(amount).to_have_value("40")
        expect(amount).to_be_visible()
        expect(submit).to_have_text("Record and Send Bill")
        expect(dialog.locator("[data-bookings-record-bill-intro]")).to_be_visible()

        _pick(dialog, world["donation"])
        expect(amount).to_have_value("25")
        expect(checkbox).to_be_checked()

        _pick(dialog, world["free"])
        expect(checkbox).not_to_be_checked()
        expect(amount).to_have_value("")
        expect(submit).to_have_text("Record Orientation")
        expect(dialog.locator("[data-bookings-record-bill-intro]")).to_be_hidden()

    def it_lets_the_orientor_untick_and_records_silently(live_server, page, login_via_code):
        world = _world(login_via_code)
        dialog = _open(page, live_server)
        _pick(dialog, world["priced"])
        dialog.locator(".pl-toggle").filter(has_text="Send a bill").click()
        expect(dialog.locator("[data-bookings-record-submit]")).to_have_text("Record Orientation")
        dialog.locator("#orientation-record-member").fill("Bryn Billable")
        dialog.locator("[data-bookings-record-submit]").click()
        expect(page.locator(".pl-orient-records [data-record-row]").filter(has_text="Bryn Billable")).to_be_visible()
        assert not OrientationBooking.objects.filter(member=world["target"]).exists()

    def it_saves_a_bill_and_shows_the_unpaid_row(live_server, page, login_via_code):
        world = _world(login_via_code)
        dialog = _open(page, live_server)
        dialog.locator("#orientation-record-member").fill("Bryn Billable")
        _pick(dialog, world["priced"])
        dialog.locator("#orientation-record-bill_amount").fill("35")
        dialog.locator("[data-bookings-record-submit]").click()
        row = page.locator("[data-booking-row]").filter(has_text="Bryn Billable")
        expect(row).to_be_visible()
        expect(row.locator('[data-booking-bill="unpaid"]')).to_have_text("Unpaid $35")
        booking = OrientationBooking.objects.get(member=world["target"])
        assert (booking.bill_amount_cents, booking.is_completed) == (3500, True)

    def it_fits_a_phone_with_no_sideways_scroll(live_server, page, login_via_code):
        page.set_viewport_size({"width": 375, "height": 812})
        world = _world(login_via_code)
        dialog = _open(page, live_server)
        _pick(dialog, world["priced"])
        expect(dialog.locator("#orientation-record-bill_amount")).to_be_visible()
        page.wait_for_function(NO_SIDEWAYS_SCROLL)


def describe_cancel_bill():
    def it_cancels_from_the_row_menu_through_its_confirm(live_server, page, login_via_code):
        world = _world(login_via_code)
        dialog = _open(page, live_server)
        dialog.locator("#orientation-record-member").fill("Bryn Billable")
        _pick(dialog, world["priced"])
        dialog.locator("[data-bookings-record-submit]").click()
        row = page.locator("[data-booking-row]").filter(has_text="Bryn Billable")
        expect(row.locator('[data-booking-bill="unpaid"]')).to_have_text("Unpaid $40")

        row.get_by_role("button", name="Actions for Bryn Billable").click()
        row.get_by_role("menuitem", name="Cancel Bill").click()
        confirm = page.get_by_role("dialog").filter(has_text="Cancel This Bill?")
        expect(confirm).to_be_visible()
        confirm.get_by_role("button", name="Cancel Bill").click()

        row = page.locator("[data-booking-row]").filter(has_text="Bryn Billable")
        expect(row.locator('[data-booking-bill="cancelled"]')).to_have_text("Bill cancelled")
        booking = OrientationBooking.objects.get(member=world["target"])
        assert booking.bill_state == "cancelled"
        assert booking.is_completed is True
