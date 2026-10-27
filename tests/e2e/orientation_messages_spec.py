"""End-to-end: an orientation booking's messages (the shared thread, core.threads).

In a real browser: a member opens Messages under their booking on the Orientations page, writes
with a photo and presses Send; the fold comes back open with the message and its photo. The
guild lead reads it on the request page's Messages card and answers; the member reads the answer
on their booking page. A decline with a note posts the note into the same thread. The photo input
is the real multi-file input inside the shared drop zone, posted through a boosted multipart form.
Waits are on what the page shows, never a snapshot after it. Set ``CAPTURE_THREAD_SHOTS=<dir>`` to
write each page at phone and desktop width in both themes. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import io
import os
from datetime import timedelta
from pathlib import Path

from django.urls import reverse
from django.utils import timezone
from PIL import Image
from playwright.sync_api import Page, expect

from membership.models import Member, OrientationBooking
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

LEAD = "thread-lead-e2e@example.com"
MEMBER = "thread-member-e2e@example.com"
SHOTS = os.environ.get("CAPTURE_THREAD_SHOTS", "")


def _png(name: str, color: tuple[int, int, int]) -> dict:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
    return {"name": name, "mimeType": "image/png", "buffer": buffer.getvalue()}


def _sign_in(page: Page, login_via_code, email: str, name: str) -> Member:
    page.context.clear_cookies()
    login_via_code(email)
    # The post-login page is the hub, which names the signed in user on <html>: wait for it, so the
    # next goto never races the session cookie (a goto too soon lands on the login page).
    page.wait_for_function("() => !!document.documentElement.dataset.plUser")
    member = Member.objects.get(user__username=email)
    member.full_legal_name = name
    member.welcome_dismissed_at = timezone.now()
    member.guided_tours_enabled = False
    member.save(update_fields=["full_legal_name", "welcome_dismissed_at", "guided_tours_enabled"])
    return member


def _capture(page: Page, name: str) -> None:
    """Write the page at phone and desktop width in both themes, when asked to."""
    if not SHOTS:
        return
    folder = Path(SHOTS)
    folder.mkdir(parents=True, exist_ok=True)
    for width in (390, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.reload()
        for theme in ("dark", "light"):
            page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
            page.screenshot(path=str(folder / f"{name}-{width}-{theme}.png"), full_page=True)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.reload()


def _world(page: Page, login_via_code) -> tuple[Member, Member, OrientationBooking]:
    """A guild lead and a member with a requested booking on the lead's guild, both signed up."""
    MembershipPlanFactory()
    lead = _sign_in(page, login_via_code, LEAD, "Lead Quillfeather")
    member = _sign_in(page, login_via_code, MEMBER, "Orla Thistlewood")
    guild = GuildFactory(name="Zzthread Woodshop", guild_lead=lead)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    orientation_type = OrientationTypeFactory(guild=guild, name="Zzbandsaw Basics")
    starts = timezone.now() + timedelta(days=3)
    slot = OrientationSlotFactory(
        guild=guild, orientation_type=orientation_type, starts_at=starts, ends_at=starts + timedelta(hours=1)
    )
    return lead, member, OrientationBookingFactory(slot=slot, member=member)


def describe_an_orientation_booking_conversation():
    def it_carries_a_member_message_with_a_photo_and_the_orientors_answer(
        live_server, page: Page, login_via_code, serve_media
    ):
        page.set_viewport_size({"width": 1280, "height": 900})
        _lead, _member, booking = _world(page, login_via_code)

        # The member writes from Messages under their booking, with a photo.
        page.goto(f"{live_server.url}{reverse('hub_orientations')}")
        fold = page.locator(f'details[data-thread-open="{booking.pk}"]')
        fold.locator("summary").click()
        composer = fold.locator("[data-thread-reply]")
        composer.locator("textarea").fill("Zzcan I bring my own blade?")
        composer.locator("input[type=file]").set_input_files([_png("blade.png", (40, 90, 160))])
        expect(composer.locator("[data-thread-photos-label]")).to_have_text("blade.png")
        composer.locator("[data-thread-send]").click()

        fold = page.locator(f'details[data-thread-open="{booking.pk}"]')
        expect(fold).to_have_attribute("open", "")
        mine = fold.locator('[data-thread-message][data-from="member"]')
        expect(mine).to_contain_text("Zzcan I bring my own blade?")
        expect(mine.locator("img")).to_have_count(1)
        expect(page.get_by_text("Sent. The Orientors for Zzthread Woodshop were notified.")).to_be_visible()
        expect(fold.locator("summary")).to_contain_text("Messages (1)")
        _capture(page, "member-card")

        # The lead reads it on the request page and answers.
        _sign_in(page, login_via_code, LEAD, "Lead Quillfeather")
        page.goto(f"{live_server.url}{reverse('hub_orientation_respond', args=[booking.pk])}")
        card = page.locator("#messages")
        expect(card.locator('[data-thread-message][data-from="member"]')).to_contain_text("Zzcan I bring my own blade?")
        expect(card.locator('[data-thread-message][data-from="member"] img')).to_have_count(1)
        card.locator("textarea").fill("Zzsure, bring it along")
        send = card.locator("[data-thread-send]")
        expect(send).to_have_text("Send to Orla Thistlewood")
        send.click()
        expect(page.get_by_text("Sent. Orla Thistlewood was notified.")).to_be_visible()
        expect(page.locator('#messages [data-thread-message][data-from="staff"]')).to_contain_text(
            "Zzsure, bring it along"
        )
        _capture(page, "request-page")

        # The member reads the answer on their booking page.
        _sign_in(page, login_via_code, MEMBER, "Orla Thistlewood")
        page.goto(f"{live_server.url}{reverse('hub_orientation_booking', args=[booking.pk])}")
        answer = page.locator('[data-thread-message][data-from="staff"]')
        expect(answer).to_contain_text("Zzsure, bring it along")
        expect(answer).to_contain_text("Lead Quillfeather")
        expect(page.locator('[data-thread-message][data-from="member"]')).to_contain_text("You")
        _capture(page, "member-booking-page")

    def it_posts_a_decline_note_into_the_thread(live_server, page: Page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        _lead, _member, booking = _world(page, login_via_code)

        _sign_in(page, login_via_code, LEAD, "Lead Quillfeather")
        page.goto(f"{live_server.url}{reverse('hub_orientation_respond', args=[booking.pk])}")
        page.locator("#decline-note").fill("Zzwe are full this month, try November")
        page.get_by_role("button", name="Decline request").click()
        expect(page.get_by_text("Orientation declined. We let the member know.")).to_be_visible()
        expect(page.locator('#messages [data-thread-message][data-from="staff"]')).to_contain_text(
            "Zzwe are full this month, try November"
        )

        _sign_in(page, login_via_code, MEMBER, "Orla Thistlewood")
        page.goto(f"{live_server.url}{reverse('hub_orientation_booking', args=[booking.pk])}")
        expect(page.locator('[data-booking-status="declined"]')).to_be_visible()
        expect(page.locator('[data-thread-message][data-from="staff"]')).to_contain_text(
            "Zzwe are full this month, try November"
        )
