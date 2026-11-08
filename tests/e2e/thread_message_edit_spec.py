"""End-to-end: an admin edits then deletes messages on a Feedback Inbox request, and the last message on a thread (#1140).

In a real browser: the admin opens the "..." on their own reply, presses Edit, rewrites it and
removes its photo, and Save redraws the one message with the new words, no photo and "edited";
then they open the "..." on the sender's reply, press Delete and confirm, and it leaves the
thread. A reload shows both stuck. A member deleting the only message on their orientation
booking sees the empty line come back in place. Run with ``pytest -m e2e`` on PostgreSQL.

Deterministic by construction. Headless Chromium can stop producing animation frames for many
seconds, on this lane and on CI's runner alike. A real click waits for frames (its stability
check), and so does Alpine, which reveals an ``x-show`` and runs transitions in
``requestAnimationFrame``. So the page's ``requestAnimationFrame`` runs on a timer
(``_FRAMES_ON_A_TIMER``), presses are dispatched clicks on controls first expected visible, waits
poll on a timer, and every step waits for the state it needs (the swapped message, then the
database), never for a transition to finish.
"""

from __future__ import annotations

import io

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from playwright.sync_api import Page, expect
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from core.models import FeedbackComment
from membership.models import Member, OrientationBookingMessage
from tests.core.factories import FeedbackRequestFactory
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationBookingFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

#: requestAnimationFrame on a 16ms timer, in the page itself: frames then never depend on the compositor.
_FRAMES_ON_A_TIMER = """
window.requestAnimationFrame = (callback) => window.setTimeout(() => callback(performance.now()), 16);
window.cancelAnimationFrame = (handle) => window.clearTimeout(handle);
"""

ADMIN = "thread-edit-admin-e2e@example.com"
SENDER = "thread-edit-sender-e2e@example.com"
MEMBER = "thread-edit-member-e2e@example.com"


def _png(name: str) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (40, 90, 160)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def _sign_in(page: Page, login_via_code, email: str, *, admin: bool = True) -> Member:
    for attempt in (1, 2):
        page.context.clear_cookies()
        login_via_code(email)
        try:
            page.wait_for_function("() => !!document.documentElement.dataset.plUser", timeout=10000, polling=100)
            break
        except PlaywrightTimeoutError:
            if attempt == 2:
                raise
    member = Member.objects.get(user__username=email)
    member.fog_role = Member.FogRole.ADMIN if admin else Member.FogRole.MEMBER
    member.welcome_dismissed_at = timezone.now()
    member.guided_tours_enabled = False
    member.save(update_fields=["fog_role", "welcome_dismissed_at", "guided_tours_enabled"])
    member.sync_user_permissions()
    return member


def describe_changing_a_feedback_reply():
    def it_edits_one_reply_in_place_and_deletes_another(live_server, page: Page, login_via_code, serve_media):
        MembershipPlanFactory()
        sender, _ = get_user_model().objects.get_or_create(
            username=SENDER, defaults={"email": SENDER, "first_name": "Sam", "last_name": "Sender"}
        )
        request = FeedbackRequestFactory(user=sender, subject="Zzthe kiln page", message="Zzthe original ask")
        page.set_viewport_size({"width": 1280, "height": 900})
        page.context.add_init_script(_FRAMES_ON_A_TIMER)
        page.emulate_media(reduced_motion="reduce")
        admin = _sign_in(page, login_via_code, ADMIN)
        assert admin.user is not None
        mine = FeedbackComment.write(
            request=request, author=admin.user, body="Zzthanks fro the report", photos=[_png("a.png")]
        )
        theirs = FeedbackComment.write(request=request, author=sender, body="Zzplease ignore this one")

        page.goto(f"{live_server.url}{reverse('hub_admin_feedback_request', args=[request.pk])}")
        # Alpine has claimed every message (its editing scope). Polled on a timer, never on animation
        # frames: headless Chromium can stop drawing frames for seconds, which also stalls a real
        # click's stability check and Alpine's transitions. So every press below is a dispatched
        # click and every wait is on the end state (the DOM, then the database), never a transition.
        page.wait_for_function(
            "() => !!window.Alpine && Array.from(document.querySelectorAll('[data-thread-dom]')).every((li) => !!li._x_dataStack)",
            polling=100,
        )
        message = page.locator(f'[data-thread-dom="{mine.dom_id}"]')
        expect(message.locator(".pl-thread-photos__item img")).to_have_count(1)

        # Edit: the menu, the box, a new wording and the photo removed.
        trigger = message.get_by_role("button", name="Edit or delete this message")
        expect(trigger).to_be_visible()
        trigger.dispatch_event("click")
        expect(trigger).to_have_attribute("aria-expanded", "true")
        message.locator("[data-thread-edit]").dispatch_event("click")
        box = message.locator("[data-thread-edit-form]")
        expect(box).to_be_visible()
        box.locator("textarea").fill("Zzthanks for the report")
        remove = box.locator(".pl-thread-edit__remove")
        expect(remove).to_be_visible()
        remove.dispatch_event("click")
        expect(box.locator("input[name=remove_photos]")).to_be_checked()
        expect(box.locator(".pl-thread-edit__remove-off")).to_be_visible()  # the button now reads Keep
        box.locator("[data-thread-edit-save]").dispatch_event("click")

        # The swap is the end state: the redrawn message carries "edited", with no photo and the box shut.
        expect(message.locator("[data-thread-edited]")).to_have_text("edited")
        expect(message.locator(".pl-thread-msg__body")).to_have_text("Zzthanks for the report")
        expect(message.locator(".pl-thread-photos__item")).to_have_count(0)
        expect(message.locator("[data-thread-edit-form]")).to_be_hidden()
        # The toast, anchored on its own markup: the changelog on every page may say "Saved." too.
        expect(page.locator(".plt-msg", has_text="Saved.")).to_have_count(1)

        # Delete: the sender's reply, behind the confirm.
        other = page.locator(f'[data-thread-dom="{theirs.dom_id}"]')
        other_trigger = other.get_by_role("button", name="Edit or delete this message")
        expect(other_trigger).to_be_visible()
        other_trigger.dispatch_event("click")
        expect(other_trigger).to_have_attribute("aria-expanded", "true")
        other.locator("[data-thread-delete]").dispatch_event("click")
        confirm = page.locator(f'form[hx-post="{theirs.delete_url}"]')
        expect(confirm).to_have_count(1)
        expect(page.get_by_role("dialog").filter(has_text="Delete this message?")).to_have_count(1)
        confirm.locator("button[type=submit]").dispatch_event("click")
        expect(other).to_have_count(0)
        expect(page.locator(".plt-msg", has_text="Deleted.")).to_have_count(1)

        page.reload()
        expect(page.locator(f'[data-thread-dom="{mine.dom_id}"] [data-thread-edited]')).to_be_visible()
        expect(page.get_by_text("Zzplease ignore this one")).to_have_count(0)
        assert not FeedbackComment.objects.filter(pk=theirs.pk).exists()
        mine.refresh_from_db()
        assert mine.body == "Zzthanks for the report" and mine.photos.count() == 0


def describe_deleting_the_last_message():
    def it_shows_the_empty_line_in_place(live_server, page: Page, login_via_code):
        MembershipPlanFactory()
        lead = MemberFactory(full_legal_name="Lark Mossbridge")
        guild = GuildFactory(guild_lead=lead, name="Zzfernbank Guild")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        orientation_type = OrientationTypeFactory(guild=guild, name="Zzplaner Basics")
        page.set_viewport_size({"width": 1280, "height": 900})
        page.context.add_init_script(_FRAMES_ON_A_TIMER)
        page.emulate_media(reduced_motion="reduce")
        member = _sign_in(page, login_via_code, MEMBER, admin=False)
        assert member.user is not None
        booking = OrientationBookingFactory(
            slot=OrientationSlotFactory(guild=guild, orientation_type=orientation_type), member=member
        )
        only = OrientationBookingMessage.write(booking=booking, author=member.user, body="Zzthe only message")

        page.goto(f"{live_server.url}{reverse('hub_orientation_booking', args=[booking.pk])}")
        page.wait_for_function(
            "() => !!window.Alpine && Array.from(document.querySelectorAll('[data-thread-dom]')).every((li) => !!li._x_dataStack)",
            polling=100,
        )
        message = page.locator(f'[data-thread-dom="{only.dom_id}"]')
        empty = page.locator("[data-thread-empty]")
        expect(empty).to_be_hidden()

        trigger = message.get_by_role("button", name="Edit or delete this message")
        expect(trigger).to_be_visible()
        trigger.dispatch_event("click")
        expect(trigger).to_have_attribute("aria-expanded", "true")
        message.locator("[data-thread-delete]").dispatch_event("click")
        confirm = page.locator(f'form[hx-post="{only.delete_url}"]')
        expect(confirm).to_have_count(1)
        confirm.locator("button[type=submit]").dispatch_event("click")

        expect(message).to_have_count(0)
        expect(empty).to_be_visible()
        assert not OrientationBookingMessage.objects.filter(pk=only.pk).exists()
