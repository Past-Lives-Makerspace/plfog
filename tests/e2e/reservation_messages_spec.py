"""End-to-end: an equipment reservation's messages (the shared thread, #974).

In a real browser: a member opens Messages from their pending reservation's row menu on the
Reservations page's Bookings tab, writes with a photo and presses Send; the page comes back with
the Messages modal open on the message and its photo. The manager sees the New message pill on
the Needs Approval card, opens Messages and answers; the member sees the pill, opens the thread
and reads the answer. A decline's reason lands in the same thread, and the member hears one
notice. The photo input is the real multi-file input inside the shared drop zone, posted
through a boosted multipart form. Waits are on what the page shows, never a snapshot after it.
Set ``CAPTURE_RESV_SHOTS=<dir>`` to write the member's modal at 390 light and the manager's at
1280 dark. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import io
import os
from datetime import timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from playwright.sync_api import Page, expect

from core.models import Notification
from membership.models import EquipmentReservation, Member
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentReservationFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
)

MANAGER = "resv-thread-manager@example.com"
MEMBER = "resv-thread-member@example.com"
SHOTS = os.environ.get("CAPTURE_RESV_SHOTS", "")


def _png(name: str, color: tuple[int, int, int]) -> dict:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
    return {"name": name, "mimeType": "image/png", "buffer": buffer.getvalue()}


def _active_member(email: str, name: str) -> Member:
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.welcome_dismissed_at = timezone.now()
    member.guided_tours_enabled = False
    member.save(update_fields=["status", "full_legal_name", "welcome_dismissed_at", "guided_tours_enabled"])
    return member


def _sign_in(page: Page, login_via_code, email: str) -> None:
    page.context.clear_cookies()
    login_via_code(email)


def _world() -> tuple[Member, EquipmentReservation]:
    """A manager of an approval tool and a member's reservation awaiting approval on it."""
    MembershipPlanFactory()  # so the user signal provisions each member
    manager = _active_member(MANAGER, "Sami Brindlewood")
    member = _active_member(MEMBER, "Juniper Wrenhallow")
    saw = EquipmentFactory(name="Zzglimmer Bandsaw", requires_approval=True)
    EquipmentStaffMembershipFactory(equipment=saw, member=manager)
    starts = timezone.now() + timedelta(days=2)
    reservation = EquipmentReservationFactory(
        equipment=saw,
        member=member,
        starts_at=starts,
        ends_at=starts + timedelta(hours=2),
        status=EquipmentReservation.Status.PENDING_APPROVAL,
        purpose="Zzresaw walnut",
    )
    return manager, reservation


def _modal(page: Page):
    return page.locator("#reservation-messages-body")


def _open_from_row_menu(page: Page, reservation: EquipmentReservation) -> None:
    page.locator(f'tr[data-reservation-row="{reservation.pk}"] .pl-row-menu__trigger').click()
    page.locator(f'[role="menuitem"][data-reservation-messages="{reservation.pk}"]').click()
    expect(_modal(page).locator(f'[data-reservation-thread="{reservation.pk}"]')).to_be_visible()


def _capture(page: Page, name: str, width: int, theme: str) -> None:
    """Write the page with its Messages modal open at one width and theme, when asked to."""
    if not SHOTS:
        return
    folder = Path(SHOTS)
    folder.mkdir(parents=True, exist_ok=True)
    page.set_viewport_size({"width": width, "height": 900})
    page.reload()
    expect(_modal(page).locator("[data-reservation-thread]")).to_be_visible()
    page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
    page.wait_for_timeout(300)
    page.screenshot(path=str(folder / f"{name}-{width}-{theme}-top.png"))
    page.locator(".pl-modal").filter(has=_modal(page)).evaluate("(el) => { el.scrollTop = el.scrollHeight; }")
    page.wait_for_timeout(200)
    page.screenshot(path=str(folder / f"{name}-{width}-{theme}.png"))
    page.set_viewport_size({"width": 1280, "height": 900})


def describe_a_reservation_conversation():
    def it_carries_a_member_message_with_a_photo_and_the_managers_answer(
        live_server, page: Page, login_via_code, serve_media
    ):
        page.set_viewport_size({"width": 1280, "height": 900})
        manager, reservation = _world()
        bookings_url = f"{live_server.url}{reverse('hub_equipment_index')}?view=bookings"

        # The member writes from Messages in their pending row's menu, with a photo.
        _sign_in(page, login_via_code, MEMBER)
        page.goto(bookings_url)
        _open_from_row_menu(page, reservation)
        expect(_modal(page).locator("[data-thread-empty]")).to_be_visible()
        composer = _modal(page).locator("[data-thread-reply]")
        composer.locator("textarea").fill("Zzis the resaw fence set up?")
        composer.locator("input[type=file]").set_input_files([_png("fence.png", (40, 90, 160))])
        expect(composer.locator("[data-thread-photos-label]")).to_have_text("fence.png")
        composer.locator("[data-thread-send]").click()

        expect(page.get_by_text("Sent. The managers of the Zzglimmer Bandsaw were notified.")).to_be_visible()
        mine = _modal(page).locator('[data-thread-message][data-from="member"]')
        expect(mine).to_contain_text("Zzis the resaw fence set up?")
        expect(mine.locator("img")).to_have_count(1)
        assert list(Notification.objects.values_list("user_id", "trigger")) == [
            (manager.user_id, "equipment.reservation_message_to_managers")
        ]
        _capture(page, "resv-member", 390, "light")

        # The manager sees the New message pill on the Needs Approval card, opens it and answers.
        _sign_in(page, login_via_code, MANAGER)
        page.goto(
            f"{live_server.url}{reverse('hub_equipment_manage', args=[reservation.equipment.slug])}?tab=reservations"
        )
        row = page.locator(f'[data-approval-row="{reservation.pk}"]')
        expect(row.locator(f'[data-thread-unread="res-{reservation.pk}"]')).to_be_visible()
        row.get_by_role("button", name="Messages").click()
        expect(row.locator("[data-thread-unread]")).to_have_count(0)
        theirs = _modal(page).locator('[data-thread-message][data-from="member"]')
        expect(theirs).to_contain_text("Zzis the resaw fence set up?")
        expect(theirs.locator("img")).to_have_count(1)
        _modal(page).locator("textarea").fill("Zzyes, set for 6 inch stock")
        _modal(page).locator("[data-thread-send]").click()
        expect(page.get_by_text("Sent. Juniper Wrenhallow was notified.")).to_be_visible()
        expect(_modal(page).locator('[data-thread-message][data-from="staff"]')).to_contain_text(
            "Zzyes, set for 6 inch stock"
        )
        _capture(page, "resv-manager", 1280, "dark")

        # The member sees the pill on their row, opens the thread and reads the answer.
        _sign_in(page, login_via_code, MEMBER)
        page.goto(bookings_url)
        expect(page.locator(f'[data-thread-unread="res-{reservation.pk}"]')).to_be_visible()
        _open_from_row_menu(page, reservation)
        answer = _modal(page).locator('[data-thread-message][data-from="staff"]')
        expect(answer).to_contain_text("Zzyes, set for 6 inch stock")
        expect(answer).to_contain_text("Sami Brindlewood")
        expect(answer).to_contain_text("Zzglimmer Bandsaw")
        expect(_modal(page).locator('[data-thread-message][data-from="member"]')).to_contain_text("You")

    def it_posts_a_decline_reason_into_the_thread_with_one_notice(live_server, page: Page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        _manager, reservation = _world()

        _sign_in(page, login_via_code, MANAGER)
        page.goto(
            f"{live_server.url}{reverse('hub_equipment_manage', args=[reservation.equipment.slug])}?tab=reservations"
        )
        page.locator(f'[data-approval-row="{reservation.pk}"]').get_by_role("button", name="Decline").click()
        dialog = page.get_by_role("dialog").filter(has_text="Decline This Reservation?")
        dialog.get_by_label("Reason").fill("Zzblade is out for sharpening that day")
        with page.expect_navigation():
            dialog.get_by_role("button", name="Decline Reservation").click()
        expect(page.locator(".plt-toast", has_text="Declined. Juniper has been emailed.")).to_be_visible()

        _sign_in(page, login_via_code, MEMBER)
        page.goto(f"{live_server.url}{reverse('hub_equipment_index')}?view=bookings&show=past")
        expect(page.locator(f'[data-thread-unread="res-{reservation.pk}"]')).to_be_visible()
        _open_from_row_menu(page, reservation)
        expect(_modal(page).locator('[data-reservation-status="declined"]')).to_be_visible()
        expect(_modal(page).locator('[data-thread-message][data-from="staff"]')).to_contain_text(
            "Zzblade is out for sharpening that day"
        )
        assert list(Notification.objects.filter(user__username=MEMBER).values_list("trigger", flat=True)) == [
            "equipment.reservation_declined"
        ]
