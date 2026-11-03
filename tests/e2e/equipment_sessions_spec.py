"""End-to-end: a machine session (#1096), from the Sessions switch to credit after Stop Session.

A manager turns on Sessions (Start and Stop) on Hours & Limits, saves, and sees the session QR
under the switch. A member with a reservation in progress sees Start Session on their Bookings
row, opens the machine's session QR URL, presses Start Session, then Stop Session, confirms, and
sees the session's times and their credit on the page. Waits are on what the page shows. Run with
``pytest -m e2e`` on PostgreSQL. Set ``SESSION_SHOTS=<folder>`` to keep the guidance shots
(``docs/GUIDANCE.md``) and the phone shot of the session page there; otherwise they go to a
temporary folder, so the spec still proves every shot can be taken.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import Equipment, EquipmentCredit, EquipmentReservation, Member
from tests.e2e.guidance import FRAMES, shoot
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
)

MANAGER_EMAIL = "equipment-session-manager@example.com"
MEMBER_EMAIL = "equipment-session-member@example.com"


def _active_member(email: str, name: str) -> Member:
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["status", "full_legal_name"])
    return member


def describe_machine_sessions():
    def it_turns_sessions_on_then_starts_and_stops_a_session_for_credit(live_server, page, login_via_code, tmp_path):
        shots = Path(os.environ.get("SESSION_SHOTS") or tmp_path)
        MembershipPlanFactory()  # so the user signal provisions each member
        manager = _active_member(MANAGER_EMAIL, "Perpetua Ashgrove")
        member = _active_member(MEMBER_EMAIL, "Linnea Brackenford")
        laser = EquipmentFactory(
            name="Thornwick Laser Cutter",
            pricing=Equipment.Pricing.HOURLY,
            hourly_rate_cents=2500,
            max_duration_minutes=480,
            early_end_credit=True,
        )
        EquipmentStaffMembershipFactory(equipment=laser, member=manager)
        starts = timezone.now() - timedelta(minutes=50)
        booking = EquipmentReservationFactory(
            equipment=laser,
            member=member,
            starts_at=starts,
            ends_at=starts + timedelta(hours=7),
            amount_paid_cents=17500,
            purpose="Cutting birch panels",
        )
        manage_url = f"{live_server.url}{reverse('hub_equipment_manage', args=[laser.slug])}"
        page.set_viewport_size(FRAMES["desktop"])

        # The manager turns Sessions on beside End Early, saves, and the session QR shows under the switch.
        login_via_code(MANAGER_EMAIL)
        page.goto(f"{manage_url}?tab=hours")
        switch = page.locator("[data-sessions-switch]")
        expect(switch).to_be_visible()
        switch.locator(".pl-toggle").click()
        with page.expect_navigation():
            page.locator("[data-pricing-card]").get_by_role("button", name="Save", exact=True).click()
        laser.refresh_from_db()
        assert laser.sessions_enabled
        expect(page.locator("[data-session-qr]")).to_be_visible()
        page.locator("[data-sessions-switch]").scroll_into_view_if_needed()
        page.evaluate("() => window.scrollBy(0, 160)")
        shoot(page, shots / "step-1.png", target="reservations.sessions-switch")

        # The member's Bookings row offers Start Session while the reservation is on.
        page.context.clear_cookies()
        login_via_code(MEMBER_EMAIL)
        page.set_viewport_size(FRAMES["desktop"])
        page.goto(f"{live_server.url}{reverse('hub_equipment_index')}?view=bookings")
        expect(page.locator(f'[data-reservation-row="{booking.pk}"] [data-session-start]')).to_be_visible()
        shoot(page, shots / "step-2.png", target="reservations.start-session")

        # At the machine: the QR opens the session page with Start Session on this reservation.
        # Loaded at phone width, as a scan is, so the sidebar starts as a closed drawer.
        session_url = f"{live_server.url}{urlparse(laser.session_qr_url).path}"
        page.set_viewport_size(FRAMES["phone"])
        page.goto(session_url)
        expect(page.locator('[data-session-page="start"]')).to_be_visible()
        expect(page.locator("[data-session-booking]")).to_be_visible()
        shoot(page, shots / "phone-session-start.png", frame="phone")
        page.set_viewport_size(FRAMES["desktop"])
        page.goto(session_url)

        page.get_by_role("button", name="Start Session", exact=True).click()
        expect(page.locator('[data-session-page="running"]')).to_be_visible()
        expect(page.locator("[data-session-running]")).to_contain_text("Started at ")
        booking.refresh_from_db()
        assert booking.session_started_at is not None
        shoot(page, shots / "step-3.png", target="reservations.stop-session")

        page.locator("[data-session-stop]").click()
        dialog = page.get_by_role("dialog").filter(has_text="Stop your session now?")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("you have not used become credit here.")
        dialog.get_by_role("button", name="Stop Session", exact=True).click()

        expect(page.locator('[data-session-page="stopped"]')).to_be_visible()
        expect(page.locator(f'[data-session-times="{booking.pk}"]')).to_contain_text(" to ")
        expect(page.locator("[data-session-credit]")).to_contain_text("hours")
        booking.refresh_from_db()
        assert booking.session_stopped_at is not None
        assert booking.status == EquipmentReservation.Status.CONFIRMED
        assert EquipmentCredit.objects.balance_for(member, laser) >= 360
