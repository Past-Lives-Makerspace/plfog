"""End-to-end: an hourly priced reservation (#749), from the Pricing card to a paid, confirmed row.

A manager picks Hourly on Hours & Limits, types $25 and sees the example line read $37.50 as
they type, then saves. A member sees "$25 per hour" beside Book a Time, picks 2:00 PM for 1.5
hours, sees "Total: $37.50" and "Reserve and Pay", and opens the "Reserve and pay?" confirm,
whose summary names the time and the amount. Continue to Payment holds the time and sends the
browser to Checkout; Stripe is patched in process so the Checkout URL is the live server's own
return page, and the session reads as paid, so the member lands on "You're Booked" and their row
reads Confirmed, Paid $37.50. Waits are on what the page shows. Run with ``pytest -m e2e`` on
PostgreSQL.
"""

from __future__ import annotations

from datetime import time, timedelta
from unittest.mock import patch
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import Equipment, EquipmentReservation, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
)

MANAGER_EMAIL = "equipment-pricing-manager@example.com"
MEMBER_EMAIL = "equipment-pricing-member@example.com"


def _active_member(email: str, name: str) -> Member:
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["status", "full_legal_name"])
    return member


def describe_hourly_pricing():
    def it_takes_a_rate_from_the_pricing_card_to_a_paid_confirmed_reservation(live_server, page, login_via_code):
        MembershipPlanFactory()  # so the user signal provisions each member
        manager = _active_member(MANAGER_EMAIL, "Sami Corvellan")
        member = _active_member(MEMBER_EMAIL, "Juniper Ashwhistle")
        router = EquipmentFactory(name="Thornquill Router")
        EquipmentStaffMembershipFactory(equipment=router, member=manager)
        day = timezone.localdate() + timedelta(days=2)
        EquipmentHoursFactory(equipment=router, weekday=day.weekday(), start_time=time(9, 0), end_time=time(17, 0))
        manage_url = f"{live_server.url}{reverse('hub_equipment_manage', args=[router.slug])}"
        detail_url = f"{live_server.url}{reverse('hub_equipment_detail', args=[router.slug])}"
        page.set_viewport_size({"width": 1100, "height": 900})

        # The manager picks Hourly, types $25, sees the live example, and saves.
        login_via_code(MANAGER_EMAIL)
        page.goto(f"{manage_url}?tab=hours")
        card = page.locator("[data-pricing-card]")
        expect(card.locator("[data-pricing-hourly]")).to_be_hidden()
        card.locator("label.pl-radio-option", has=page.locator('[data-pricing-option="hourly"]')).click()
        expect(card.locator("[data-pricing-hourly]")).to_be_visible()
        card.get_by_label("Hourly rate").fill("25")
        expect(card.locator("[data-hourly-example]")).to_have_text("A 1 hour 30 minute reservation costs $37.50.")
        with page.expect_navigation():
            page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator('[data-pricing-option="hourly"]')).to_be_checked()
        router.refresh_from_db()
        assert (router.pricing, router.hourly_rate_cents) == (Equipment.Pricing.HOURLY, 2500)

        # The member sees the chip and the total, and opens the priced confirm.
        page.context.clear_cookies()
        login_via_code(MEMBER_EMAIL)
        page.goto(detail_url)
        expect(page.locator("[data-price-chip]")).to_have_text("$25 per hour")
        form = page.locator("#equip-reserve-form")
        form.locator("select[name=starts_at]").select_option(label="2:00 PM")
        form.locator("select[name=duration_minutes]").select_option(label="1.5 hours")
        expect(page.locator("[data-reserve-total]")).to_have_text("Total: $37.50")
        reserve = page.get_by_role("button", name="Reserve and Pay", exact=True)
        expect(reserve).to_be_visible()

        def checkout(**kwargs):
            # Checkout's hosted page is skipped: its success_url, on this live server, is the next stop.
            return {"id": "cs_e2e_749", "url": f"{live_server.url}{urlparse(kwargs['success_url']).path}"}

        paid = {
            "id": "cs_e2e_749",
            "url": "",
            "status": "complete",
            "payment_status": "paid",
            "payment_intent": "pi_e2e_749",
            "amount_total": 3750,
        }
        with (
            patch("billing.stripe_utils.create_checkout_session", side_effect=checkout) as create,
            patch("billing.stripe_utils.retrieve_checkout_session", return_value=paid),
        ):
            reserve.click()
            dialog = page.get_by_role("dialog").filter(has_text="Reserve and pay?")
            expect(dialog).to_be_visible()
            expect(dialog.locator("[data-confirm-summary]")).to_contain_text("2:00 PM to 3:30 PM · $37.50")
            expect(dialog).to_contain_text("You'll pay $37.50 now through our secure checkout")
            with page.expect_navigation(url=lambda url: "/checkout/" in url):
                dialog.get_by_role("button", name="Continue to Payment").click()
            expect(page.locator('[data-checkout-state="confirmed"]')).to_have_text("You're Booked")
            expect(page.get_by_text("You paid $37.50. Your reservation is confirmed.")).to_be_visible()
        assert create.call_args.kwargs["amount_cents"] == 3750

        # Back on the equipment page, the row reads Confirmed and paid.
        page.goto(detail_url)
        row = page.locator('[data-my-reservation-state="confirmed"]')
        expect(row).to_contain_text("Confirmed")
        expect(row).to_contain_text("Paid $37.50")
        reservation = EquipmentReservation.objects.get(member=member)
        assert reservation.status == EquipmentReservation.Status.CONFIRMED
        assert (reservation.amount_paid_cents, reservation.stripe_payment_id) == (3750, "pi_e2e_749")
