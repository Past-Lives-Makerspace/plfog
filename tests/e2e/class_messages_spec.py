"""End-to-end: a class's Messages between its instructor and its reviewer (#975).

In a real browser: the guild lead opens the review link signed in, asks for changes with a
note, and the note shows in the review page's Messages; the instructor gets exactly the
decision's notice. The instructor opens the class page, reads the note in Messages and answers
with a photo without resubmitting; the class stays a draft and the lead is notified once. The
lead follows the messages link back to the review page and reads the answer with its photo.
Waits are on what the page shows. Set ``CAPTURE_THREAD_SHOTS=<dir>`` to write each page at phone
and desktop width in both themes. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import io
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from playwright.sync_api import Page, expect

from classes.factories import CategoryFactory, ClassOfferingFactory, InstructorFactory
from classes.models import ClassApproval, ClassOffering
from core.events.registry import CLASS_MESSAGE_TO_INSTRUCTORS, CLASS_MESSAGE_TO_REVIEWERS
from core.models import Notification
from membership.models import Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

LEAD = "cls-thread-lead-e2e@example.com"
TEACHER = "cls-thread-teacher-e2e@example.com"
SHOTS = os.environ.get("CAPTURE_THREAD_SHOTS", "")


def _png(name: str, color: tuple[int, int, int]) -> dict:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
    return {"name": name, "mimeType": "image/png", "buffer": buffer.getvalue()}


def _sign_in(page: Page, login_via_code, email: str, name: str) -> Member:
    page.context.clear_cookies()
    login_via_code(email)
    member = Member.objects.get(user__username=email)
    member.full_legal_name = name
    member.status = Member.Status.ACTIVE
    member.welcome_dismissed_at = timezone.now()
    member.guided_tours_enabled = False
    member.save(update_fields=["full_legal_name", "status", "welcome_dismissed_at", "guided_tours_enabled"])
    return member


def _capture(page: Page, name: str) -> None:
    """Write the page at phone and desktop width in both themes, when asked to."""
    if not SHOTS:
        return
    folder = Path(SHOTS)
    folder.mkdir(parents=True, exist_ok=True)
    # Without ?messages=, so the page is not scrolled under its sticky bar for a full page shot.
    bare = page.url.split("?")[0]
    for width in (390, 1280):
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(bare)
        for theme in ("dark", "light"):
            page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
            page.screenshot(path=str(folder / f"cls-{name}-{width}-{theme}.png"), full_page=True)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.goto(bare)


def describe_a_class_review_conversation():
    def it_carries_the_change_request_note_and_the_instructors_answer_without_a_resubmit(
        live_server, page: Page, login_via_code, serve_media
    ):
        page.set_viewport_size({"width": 1280, "height": 900})
        MembershipPlanFactory()
        lead = _sign_in(page, login_via_code, LEAD, "Jay Holtman")
        _sign_in(page, login_via_code, TEACHER, "Erin Horgan")
        teacher = InstructorFactory(
            user=get_user_model().objects.get(username=TEACHER), full_legal_name="Erin Horgan", instructor_slug="erin"
        )
        guild = GuildFactory(name="Zzthread Ceramics", guild_lead=lead)
        offering = ClassOfferingFactory(
            title="Zz Make Two Mugs", category=CategoryFactory(guild=guild), instructors=[teacher], ready=True
        )
        (gate,) = offering.submit_for_review()

        # The lead asks for changes with a note; it shows in the review page's Messages.
        _sign_in(page, login_via_code, LEAD, "Jay Holtman")
        page.goto(f"{live_server.url}{gate.review_path}")
        page.get_by_label("Ask for changes").check()
        page.get_by_label("Notes for the instructor").fill("Zzplease add a photo of a finished mug")
        page.get_by_role("button", name="Submit", exact=True).click()
        expect(page.get_by_text("Your decision has been recorded.")).to_be_visible()
        note = page.locator('#messages [data-thread-message][data-from="staff"]')
        expect(note).to_contain_text("Zzplease add a photo of a finished mug")
        # One notice: the decision's own; writing its note into the thread told nobody.
        teacher_bells = Notification.objects.filter(user=teacher.user)
        assert teacher_bells.filter(trigger="instructor_changes_requested").count() == 1
        assert teacher_bells.filter(trigger=CLASS_MESSAGE_TO_INSTRUCTORS).count() == 0

        # The instructor reads it on the class page and answers with a photo, without resubmitting.
        _sign_in(page, login_via_code, TEACHER, "Erin Horgan")
        page.goto(f"{live_server.url}{reverse('classes:teach_class_detail', kwargs={'pk': offering.pk})}")
        card = page.locator("#messages")
        expect(card.locator('[data-thread-message][data-from="staff"]')).to_contain_text(
            "Zzplease add a photo of a finished mug"
        )
        expect(card.locator('[data-thread-message][data-from="staff"]')).to_contain_text("Jay Holtman")
        card.locator("textarea").fill("Zzadded one, is the glaze shot enough?")
        card.locator("input[type=file]").set_input_files([_png("mug.png", (150, 90, 40))])
        expect(card.locator("[data-thread-photos-label]")).to_have_text("mug.png")
        card.locator("[data-thread-send]").click()
        expect(page.get_by_text("Sent. The Zzthread Ceramics lead and Class Reviewers were notified.")).to_be_visible()
        mine = page.locator('#messages [data-thread-message][data-from="member"]')
        expect(mine).to_contain_text("Zzadded one, is the glaze shot enough?")
        expect(mine.locator("img")).to_have_count(1)
        offering.refresh_from_db()
        assert offering.status == ClassOffering.Status.DRAFT
        assert ClassApproval.objects.filter(class_offering=offering, notes__contains="Zzplease").exists()
        assert Notification.objects.filter(user=lead.user, trigger=CLASS_MESSAGE_TO_REVIEWERS).count() == 1
        assert Notification.objects.filter(trigger=CLASS_MESSAGE_TO_INSTRUCTORS).count() == 0
        _capture(page, "instructor-overview")

        # The lead follows the messages link back to the review page and reads the answer.
        _sign_in(page, login_via_code, LEAD, "Jay Holtman")
        page.goto(f"{live_server.url}{offering.messages_path}")
        expect(page).to_have_url(f"{live_server.url}{gate.review_path}?messages=class-{offering.pk}")
        answer = page.locator('#messages [data-thread-message][data-from="member"]')
        expect(answer).to_contain_text("Zzadded one, is the glaze shot enough?")
        expect(answer).to_contain_text("Erin Horgan")
        expect(answer.locator("img")).to_have_count(1)
        _capture(page, "review-page")
