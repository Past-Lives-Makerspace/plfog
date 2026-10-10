"""End-to-end: equipment that needs a manager's approval (#748), request to decision in a real browser.

A manager turns Needs approval on from Hours & Limits and saves, and the switch reads on. A
member then sends two requests from Book a Time through the "Request this time?" confirm and
sees both waiting on Your Reservations Here. The manager approves one from the Needs Approval
card with no confirm, and declines the other through the decline modal, whose button stays
disabled until a reason is typed. Waits are on what the page shows (the boosted arrival trap).
Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import time, timedelta

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import EquipmentReservation, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentHoursFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
)

MANAGER_EMAIL = "equipment-approvals-manager@example.com"
MEMBER_EMAIL = "equipment-approvals-member@example.com"


def _active_member(email: str, name: str) -> Member:
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["status", "full_legal_name"])
    return member


def describe_equipment_that_needs_approval():
    def it_takes_a_request_from_the_switch_to_an_approval_and_a_decline(live_server, page, login_via_code):
        MembershipPlanFactory()  # so the user signal provisions each member
        manager = _active_member(MANAGER_EMAIL, "Sami Brindlewood")
        _active_member(MEMBER_EMAIL, "Juniper Wrenhallow")
        lathe = EquipmentFactory(name="Glimmerforge Lathe")
        EquipmentStaffMembershipFactory(equipment=lathe, member=manager)
        day = timezone.localdate() + timedelta(days=2)
        EquipmentHoursFactory(equipment=lathe, weekday=day.weekday(), start_time=time(9, 0), end_time=time(17, 0))
        manage_url = f"{live_server.url}{reverse('hub_equipment_manage', args=[lathe.slug])}"
        detail_url = f"{live_server.url}{reverse('hub_equipment_detail', args=[lathe.slug])}"
        page.set_viewport_size({"width": 1100, "height": 900})

        # The manager turns Needs approval on and saves; the page shows it on.
        login_via_code(MANAGER_EMAIL)
        page.goto(f"{manage_url}?tab=hours")
        switch = page.locator("[data-needs-approval] input[type=checkbox]")
        expect(switch).not_to_be_checked()
        page.locator("[data-needs-approval] .pl-toggle").click()
        with page.expect_navigation():
            page.get_by_role("button", name="Save", exact=True).click()
        expect(page.locator("[data-needs-approval] input[type=checkbox]")).to_be_checked()
        lathe.refresh_from_db()
        assert lathe.requires_approval is True

        # The member sends two requests through the request confirm.
        page.context.clear_cookies()
        login_via_code(MEMBER_EMAIL)
        page.goto(detail_url)
        expect(page.locator("[data-approval-terms]")).to_be_visible()
        for start in ("9:00 AM", "1:00 PM"):
            page.locator("#equip-reserve-form select[name=starts_at]").select_option(label=start)
            page.get_by_role("button", name="Reserve", exact=True).click()
            dialog = page.get_by_role("dialog").filter(has_text="Request this time?")
            expect(dialog).to_be_visible()
            dialog.get_by_role("button", name="Send request").click()
            expect(page.locator(".plt-toast", has_text="Request sent.").last).to_be_visible()
        expect(page.locator('[data-my-reservation-state="pending_approval"]')).to_have_count(2)
        expect(page.locator("[data-pending-slot]")).to_have_count(2)
        first, second = EquipmentReservation.objects.filter(equipment=lathe).order_by("starts_at")
        assert first.status == second.status == EquipmentReservation.Status.PENDING_APPROVAL

        # The manager approves the first with no confirm and declines the second with a reason.
        page.context.clear_cookies()
        login_via_code(MANAGER_EMAIL)
        page.goto(f"{manage_url}?tab=reservations")
        card = page.locator("[data-needs-approval-card]")
        expect(card.locator("[data-approval-row]")).to_have_count(2)
        with page.expect_navigation():
            card.locator(f'[data-approval-row="{first.pk}"]').get_by_role("button", name="Approve").click()
        expect(page.locator(".plt-toast", has_text="Approved. Juniper has been emailed.")).to_be_visible()
        expect(card.locator("[data-approval-row]")).to_have_count(1)

        card.locator(f'[data-approval-row="{second.pk}"]').get_by_role("button", name="Decline").click()
        dialog = page.get_by_role("dialog").filter(has_text="Decline This Reservation?")
        expect(dialog).to_be_visible()
        confirm = dialog.get_by_role("button", name="Decline Reservation")
        expect(confirm).to_be_disabled()
        dialog.get_by_label("Reason").fill("The spindle is out for repair that week.")
        expect(confirm).to_be_enabled()
        with page.expect_navigation():
            confirm.click()
        expect(page.locator(".plt-toast", has_text="Declined. Juniper has been emailed.")).to_be_visible()
        expect(card.locator("[data-approval-row]")).to_have_count(0)
        expect(card.get_by_text("Nothing is waiting for approval.")).to_be_visible()

        first.refresh_from_db()
        second.refresh_from_db()
        assert first.status == EquipmentReservation.Status.CONFIRMED
        assert second.status == EquipmentReservation.Status.DECLINED
        assert second.cancelled_reason == "The spindle is out for repair that week."
