"""End-to-end: a feedback request becomes a conversation.

In a real browser: an admin opens a member's request in the Feedback inbox, writes a comment
with a photo and presses Send to <sender>; the sender opens the Feedback page, reads it in the
request's thread and replies with a photo of their own; back in the inbox the request carries
the Member replied pill and the Replies filter finds it. The photo inputs are real multi-file
inputs inside the shared drop zone, posted through a boosted multipart form, and a double clicked
Send Reply posts once. Waits are on what the page shows, never a
snapshot after it. Set ``CAPTURE_FEEDBACK_SHOTS=<dir>`` to write each page at phone and desktop
width in both themes. Run with ``pytest -m e2e`` on PostgreSQL.
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
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from core.models import FeedbackRequest
from membership.models import Member
from tests.core.factories import FeedbackRequestFactory
from tests.membership.factories import MembershipPlanFactory

ADMIN = "feedback-admin-e2e@example.com"
SENDER = "feedback-sender-e2e@example.com"
SHOTS = os.environ.get("CAPTURE_FEEDBACK_SHOTS", "")


def _png(name: str, color: tuple[int, int, int]) -> dict:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
    return {"name": name, "mimeType": "image/png", "buffer": buffer.getvalue()}


def _sign_in(page: Page, login_via_code, email: str, *, fog_role: str = Member.FogRole.MEMBER) -> None:
    # The post-login page is the hub, which names the signed in user on <html>: wait for it, so the
    # next goto never races the session cookie. A sign in that lands signed out (seen about once in
    # twenty runs here) is tried once more rather than failing a later, unrelated step.
    for attempt in (1, 2):
        page.context.clear_cookies()
        login_via_code(email)
        try:
            page.wait_for_function("() => !!document.documentElement.dataset.plUser", timeout=10000)
            break
        except PlaywrightTimeoutError:
            if attempt == 2:
                raise
    member = Member.objects.get(user__username=email)
    member.fog_role = fog_role
    member.welcome_dismissed_at = timezone.now()
    member.guided_tours_enabled = False
    member.save(update_fields=["fog_role", "welcome_dismissed_at", "guided_tours_enabled"])
    member.sync_user_permissions()


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


def describe_a_feedback_conversation():
    def it_carries_an_admin_comment_and_a_member_reply_both_with_photos(
        live_server, page: Page, login_via_code, serve_media
    ):
        MembershipPlanFactory()
        sender, _ = get_user_model().objects.get_or_create(
            username=SENDER, defaults={"email": SENDER, "first_name": "Sam", "last_name": "Sender"}
        )
        request = FeedbackRequestFactory(user=sender, subject="Zzbooking from my phone", message="Zzthe original ask")
        page.set_viewport_size({"width": 1280, "height": 900})

        # The admin answers with a photo.
        _sign_in(page, login_via_code, ADMIN, fog_role=Member.FogRole.ADMIN)
        page.goto(f"{live_server.url}{reverse('hub_admin_feedback_request', args=[request.pk])}")
        comment_form = page.locator("[data-thread-reply]")
        comment_form.locator("textarea").fill("Zzwhich phone is this on?")
        comment_form.locator("input[type=file]").set_input_files([_png("admin-shot.png", (40, 90, 160))])
        send = comment_form.locator("[data-thread-send]")
        expect(send).to_have_text("Send to Sam Sender")
        send.click()
        staff_comment = page.locator('[data-thread-message][data-from="staff"]')
        expect(staff_comment).to_contain_text("Zzwhich phone is this on?")
        expect(staff_comment.locator("img")).to_have_count(1)
        expect(page.get_by_text("Sent. Sam Sender was notified.")).to_be_visible()
        _capture(page, "admin-request")

        # The sender reads it in their request's thread and replies with a photo.
        _sign_in(page, login_via_code, SENDER)
        page.goto(f"{live_server.url}{reverse('hub_beta_feedback')}#request-{request.pk}")
        row = page.locator(f'[data-feedback-request="{request.pk}"]')
        expect(row).to_have_attribute("open", "")
        expect(row.locator('[data-thread-message][data-from="staff"]')).to_contain_text("Zzwhich phone is this on?")
        expect(row.locator('[data-thread-message][data-from="staff"] img')).to_have_count(1)
        reply = row.locator("[data-thread-reply]")
        reply.locator("textarea").fill("Zzit is a Pixel 8")
        reply.locator("input[type=file]").set_input_files([_png("my-shot.png", (160, 60, 40))])
        expect(reply.locator("[data-thread-photos-label]")).to_have_text("my-shot.png")
        # The busy guard: hold the first request in the browser and Send Reply is already
        # disabled and reads Sending..., so a second click has nothing to press.
        page.evaluate(
            "() => document.addEventListener('htmx:beforeRequest', (e) => { if (window.plHoldReply) e.preventDefault(); })"
        )
        page.evaluate("() => { window.plHoldReply = true; }")
        send_reply = reply.locator("button[type=submit]")
        send_reply.click()
        expect(send_reply).to_be_disabled()
        expect(send_reply).to_have_text("Sending...")
        assert request.comments.filter(author=sender).count() == 0
        page.reload()
        row = page.locator(f'[data-feedback-request="{request.pk}"]')
        reply = row.locator("[data-thread-reply]")
        reply.locator("textarea").fill("Zzit is a Pixel 8")
        reply.locator("input[type=file]").set_input_files([_png("my-shot.png", (160, 60, 40))])
        reply.get_by_role("button", name="Send Reply").dblclick()
        row = page.locator(f'[data-feedback-request="{request.pk}"]')
        mine = row.locator('[data-thread-message][data-from="member"]')
        expect(mine).to_contain_text("Zzit is a Pixel 8")
        expect(mine.locator("img")).to_have_count(1)
        expect(row).to_have_attribute("open", "")
        expect(page.get_by_text("Sent. Past Lives will see your reply.")).to_be_visible()
        assert request.comments.filter(author=sender).count() == 1
        _capture(page, "sender-feedback")

        # Back in the inbox, the request waits under Member replied.
        _sign_in(page, login_via_code, ADMIN, fog_role=Member.FogRole.ADMIN)
        page.goto(f"{live_server.url}{reverse('hub_admin_feedback_inbox')}")
        inbox_row = page.locator(f'[data-feedback-row="{request.pk}"]')
        expect(inbox_row.locator("[data-feedback-replied]")).to_have_text("Member replied")
        page.select_option("#feedback-replied", "1")
        page.get_by_role("button", name="Apply").click()
        page.wait_for_url(lambda url: "replied=1" in url)
        # The filtered page itself, not the unfiltered one it replaces: only it has Clear.
        expect(page.get_by_role("link", name="Clear")).to_be_visible()
        expect(page.locator(f'[data-feedback-row="{request.pk}"]')).to_be_visible()
        _capture(page, "admin-inbox")

        # Opening it clears the pill. Wait on the request page's own composer (the inbox has
        # none) before anything else, then on the inbox showing the pill gone, before the database.
        page.locator(f'[data-feedback-row="{request.pk}"] a').first.click()
        page.wait_for_url(lambda url: f"/manage/feedback/{request.pk}/" in url)
        expect(page.locator("[data-thread-reply] [data-thread-send]")).to_have_text("Send to Sam Sender")
        expect(page.locator('[data-thread-message][data-from="member"]')).to_contain_text("Zzit is a Pixel 8")
        # A reply commits, then its on_commit stamps member_replied_at; an admin GET landing in
        # between shows the reply but marks the request seen just before the stamp, so the pill
        # stays (the accepted race from #966's review: a false positive, never a lost reply).
        # Open the request again until the filtered inbox drops it, for up to about 5 seconds.
        inbox = f"{live_server.url}{reverse('hub_admin_feedback_inbox')}?replied=1"
        request_page = f"{live_server.url}{reverse('hub_admin_feedback_request', args=[request.pk])}"
        listed = page.locator(f'[data-feedback-row="{request.pk}"]')
        for _attempt in range(10):
            page.goto(inbox)
            expect(page.locator("[data-feedback-inbox], [data-feedback-inbox-empty]")).to_be_visible()
            if listed.count() == 0:
                break
            page.goto(request_page)
            expect(page.locator("[data-thread-reply] [data-thread-send]")).to_have_text("Send to Sam Sender")
            page.wait_for_timeout(500)
        expect(listed).to_have_count(0)
        assert not FeedbackRequest.objects.member_replied().exists()
        assert request.comments.filter(author=sender).count() == 1


def describe_closing_feedback():
    def it_closes_a_bug_as_not_a_bug_closes_feedback_and_keeps_a_closed_reply_in_view(
        live_server, page: Page, login_via_code
    ):
        MembershipPlanFactory()
        sender, _ = get_user_model().objects.get_or_create(
            username=SENDER, defaults={"email": SENDER, "first_name": "Sam", "last_name": "Sender"}
        )
        bug = FeedbackRequestFactory(user=sender, category="bug", subject="Zzbook button does nothing")
        idea = FeedbackRequestFactory(user=sender, category="feedback", subject="Zzlove the new shop")
        page.set_viewport_size({"width": 1280, "height": 900})
        _sign_in(page, login_via_code, ADMIN, fog_role=Member.FogRole.ADMIN)

        # Not a bug: the box starts with the opener; emptied, the browser refuses to send it.
        page.goto(f"{live_server.url}{reverse('hub_admin_feedback_request', args=[bug.pk])}")
        page.locator("[data-feedback-not-a-bug]").click()
        resolve = page.locator('[data-feedback-resolve-form="not_a_bug"]')
        comment = resolve.locator("textarea")
        expect(comment).to_have_value(FeedbackRequest.NOT_A_BUG_OPENER)
        comment.fill("")
        resolve.get_by_role("button", name="Mark Not a Bug").click()
        expect(comment).to_be_visible()
        assert FeedbackRequest.objects.get(pk=bug.pk).status == "received"
        comment.fill(FeedbackRequest.NOT_A_BUG_OPENER + "tap the time first, then Book.")
        resolve.get_by_role("button", name="Mark Not a Bug").click()
        expect(page.get_by_text("Saved. Sam Sender was notified.")).to_be_visible()
        expect(page.locator(".pl-feedback-admin__meta [data-feedback-status]")).to_have_text("Not a bug")
        expect(page.locator('[data-thread-message][data-from="staff"]')).to_contain_text("tap the time first")

        # Close: general feedback, no comment needed.
        page.goto(f"{live_server.url}{reverse('hub_admin_feedback_request', args=[idea.pk])}")
        page.locator("[data-feedback-close]").click()
        page.locator('[data-feedback-resolve-form="close"]').get_by_role("button", name="Close Request").click()
        expect(page.locator(".pl-feedback-admin__meta [data-feedback-status]")).to_have_text("Closed")

        # The inbox opens on Open, so both are out of the way; All closed brings them back.
        page.goto(f"{live_server.url}{reverse('hub_admin_feedback_inbox')}")
        expect(page.locator("[data-feedback-inbox-empty]")).to_be_visible()
        page.select_option("#feedback-status", "closed_all")
        page.get_by_role("button", name="Apply").click()
        page.wait_for_url(lambda url: "status=closed_all" in url)
        expect(page.locator(f'[data-feedback-row="{bug.pk}"]')).to_be_visible()
        expect(page.locator(f'[data-feedback-row="{idea.pk}"]')).to_be_visible()

        # The sender replies on the closed bug: it stays Not a bug, and says it is closed.
        _sign_in(page, login_via_code, SENDER)
        page.goto(f"{live_server.url}{reverse('hub_beta_feedback')}?sent={bug.pk}#request-{bug.pk}")
        row = page.locator(f'[data-feedback-request="{bug.pk}"]')
        expect(row.locator("[data-feedback-closed-line]")).to_have_text(
            "This request is closed. Reply here if you still need help."
        )
        reply = row.locator("[data-thread-reply]")
        reply.locator("textarea").fill("Zzthat did not work for me")
        reply.get_by_role("button", name="Send Reply").click()
        row = page.locator(f'[data-feedback-request="{bug.pk}"]')
        expect(row.locator('[data-thread-message][data-from="member"]')).to_contain_text("Zzthat did not work")
        expect(row.locator("[data-feedback-status]")).to_have_text("Not a bug")

        # Back in the inbox, the default Open view shows it with Member replied.
        _sign_in(page, login_via_code, ADMIN, fog_role=Member.FogRole.ADMIN)
        page.goto(f"{live_server.url}{reverse('hub_admin_feedback_inbox')}")
        inbox_row = page.locator(f'[data-feedback-row="{bug.pk}"]')
        expect(inbox_row.locator("[data-feedback-replied]")).to_have_text("Member replied")
        expect(page.locator(f'[data-feedback-row="{idea.pk}"]')).to_have_count(0)
        assert FeedbackRequest.objects.get(pk=bug.pk).status == "not_a_bug"
