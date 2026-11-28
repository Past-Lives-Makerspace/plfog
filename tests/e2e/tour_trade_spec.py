"""End-to-end: guides trade a tour, and it moves only when the other guide accepts (#1308).

Gina opens Trade this tour on Sam's 4:00 tour, picks Dayzie, adds a note and sends it. Her row reads
"Trade offered to Dayzie Dove, waiting" with Withdraw, and the tour is still hers. Dayzie signs in,
sees the same row with the note, Accept Trade and Decline, and accepts: the tour is hers, and Sam
gets one email saying his guide is now Dayzie. ``CAPTURE_1308_DIR=<dir>`` saves the modal, both
waiting rows and the guest's email, dark, at desktop (1280x800) and phone (390) widths;
``GUIDANCE_1308_DIR=<dir>`` saves the What's New shots at the standard frame. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import os
from datetime import time, timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from membership.models import AdminCapability, Member
from tests.e2e.guidance import shoot
from tests.membership.factories import MembershipPlanFactory
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local

GINA_EMAIL = "tour-guide-trade-gina@example.com"
DAYZIE_EMAIL = "tour-guide-trade-dayzie@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_1308_DIR")
GUIDANCE_DIR = os.environ.get("GUIDANCE_1308_DIR")
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}


def _capture(page, name: str, focus: str, *, whole: bool = False) -> None:
    """Dark, at desktop and phone widths with ``focus`` scrolled into view, when CAPTURE_1308_DIR is set."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    page.evaluate("() => document.querySelectorAll('.plt-toast').forEach((el) => el.remove())")
    for label, viewport in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(viewport)
        page.evaluate(
            "() => window.Alpine && document.body._x_dataStack && (Alpine.$data(document.body).sidebarOpen = false)"
        )
        page.wait_for_timeout(400)
        page.locator(focus).first.scroll_into_view_if_needed()
        page.screenshot(path=str(Path(CAPTURE_DIR) / f"1308-{name}-{label}-dark.png"), full_page=whole)
    page.set_viewport_size(DESKTOP)


def _guidance_shot(page, step: int, selector: str) -> None:
    """Step ``step`` of the fragment's shots with ``selector`` ringed in gold, when GUIDANCE_1308_DIR is set."""
    if not GUIDANCE_DIR:
        return
    page.evaluate("() => document.querySelectorAll('.plt-toast').forEach((el) => el.remove())")
    control = page.locator(selector).first
    control.evaluate("(el) => el.setAttribute('data-guidance-target', 'shot.step')")
    control.scroll_into_view_if_needed()
    shoot(page, Path(GUIDANCE_DIR) / f"step-{step}.png", target="shot.step")
    control.evaluate("(el) => el.removeAttribute('data-guidance-target')")


def _settled(page, selector: str) -> None:
    """Wait for ``selector`` in an htmx swap and for htmx to finish settling it (see tours_upcoming_spec)."""
    page.locator(selector).first.wait_for()
    page.wait_for_function("() => !document.querySelector('.htmx-settling, .htmx-swapping, .htmx-request')")


def _make_guide(email: str, name: str) -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["status", "full_legal_name"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    return member


def describe_trade_a_tour():
    def it_moves_only_when_the_other_guide_accepts(live_server, page, login_via_code):
        gina = _make_guide(GINA_EMAIL, "Gina Guide")
        dayzie = _make_guide(DAYZIE_EMAIL, "Dayzie Dove")
        day = timezone.localdate() + timedelta(days=3)
        TourHoursFactory(guide=gina, weekday=day.weekday(), start_time=time(16), end_time=time(18), seats=4)
        sam = TourBookingFactory(
            guide=gina,
            starts_at=local(day, 16),
            party_size=2,
            visitor_name="Sam Rivera",
            visitor_email="sam@example.com",
        )
        upcoming = f"{live_server.url}{reverse('tours:upcoming')}"
        row = f'[data-tour-booking="{sam.pk}"]'

        # Gina offers Sam's tour to Dayzie, with a note.
        login_via_code(GINA_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(upcoming)
        page.locator(row).wait_for()
        page.locator(f"{row} .pl-row-menu__trigger").click()
        page.locator(f'[data-tour-trade-open="{sam.pk}"]').click()
        modal = page.locator("#tour-trade-body")
        _settled(page, "#tour-trade-body [data-tour-trade-to]")
        modal.locator("[data-tour-trade-to]").select_option(str(dayzie.pk))
        modal.locator('textarea[name="note"]').fill("I have a class that afternoon. Thank you!")
        _capture(page, "modal", "[data-tour-trade-send]")
        _guidance_shot(page, 1, "[data-tour-trade-send]")
        with page.expect_navigation():
            modal.locator("[data-tour-trade-send]").click()
        page.locator(".plt-msg", has_text="We offered Sam Rivera's tour to Dayzie").first.wait_for()

        # It is still Gina's, waiting on Dayzie.
        waiting = page.locator(f"{row} [data-tour-trade-waiting]")
        waiting.wait_for()
        assert waiting.inner_text() == "Trade offered to Dayzie Dove, waiting"
        assert page.locator(f"{row} [data-tour-booking-guide]").inner_text() == "You"
        page.locator(f'[data-tour-trade-withdraw="{sam.pk}"]').wait_for()
        _capture(page, "waiting-gina", f"{row} [data-tour-trade-withdraw]")
        _guidance_shot(page, 2, f"{row} [data-tour-trade-waiting]")
        sam.refresh_from_db()
        assert (sam.guide, sam.trade_to) == (gina, dayzie)

        # Dayzie sees the note with Accept Trade and Decline, and accepts.
        page.context.clear_cookies()
        login_via_code(DAYZIE_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(upcoming)
        page.locator(f'[data-tour-trade-accept="{sam.pk}"]').wait_for()
        assert page.locator(f"{row} [data-tour-trade-waiting]").inner_text() == "Trade offered to you, waiting"
        assert "I have a class that afternoon." in page.locator(f"{row} [data-tour-trade-note]").inner_text()
        _capture(page, "answer-dayzie", f'[data-tour-trade-accept="{sam.pk}"]')
        _guidance_shot(page, 3, f'[data-tour-trade-accept="{sam.pk}"]')
        mail.outbox = []
        with page.expect_navigation():
            page.locator(f'[data-tour-trade-accept="{sam.pk}"]').click()
        page.locator(".plt-msg", has_text="is yours now").first.wait_for()
        page.locator(f"{row} [data-tour-booking-guide]", has_text="You").wait_for()
        assert page.locator(f"{row} [data-tour-trade-waiting]").count() == 0
        sam.refresh_from_db()
        assert (sam.guide, sam.has_open_trade) == (dayzie, False)

        # Sam is told who his guide is now, once.
        (fyi,) = [message for message in mail.outbox if message.to == ["sam@example.com"]]
        assert fyi.subject == "Your Past Lives tour guide is now Dayzie"
        if CAPTURE_DIR:
            html = next(body for body, kind in fyi.alternatives if kind == "text/html")
            page.set_content(str(html))
            _capture(page, "guest-email", "body", whole=True)
