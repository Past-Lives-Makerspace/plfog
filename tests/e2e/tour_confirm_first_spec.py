"""End-to-end: a Tour Guide picks Confirm First, then confirms one request and declines another (#1166).

The guide sets Confirm First on My Hours. Two visitors book on the website, so both wait as
requests: the guide's email offers Confirm and Decline, and Upcoming shows each request with those
buttons. Confirm books the first (the visitor gets the confirmation with its calendar invite);
Decline, with a message, frees the second's seats and tells that visitor. On the tours host a
visitor who books such a guide's tour lands on Request Sent with Cancel My Request alone. At a
phone's width nothing scrolls sideways. ``CAPTURE_1166_DIR=<dir>`` saves dark and light pictures at
desktop (1100) and phone (390) widths of the card, Upcoming, the guide's email and the visitor's
page, and the guidance shots at the standard frame. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from membership.models import AdminCapability, Member
from tests.e2e.guidance import shoot
from tests.membership.factories import MembershipPlanFactory
from tests.tours.factories import TourHoursFactory, local
from tours.booking import Visitor, book_tour
from tours.models import TourBooking, TourGuideSettings

GUIDE_EMAIL = "tour-guide-confirm-first@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_1166_DIR")
DESKTOP = {"width": 1100, "height": 1000}
PHONE = {"width": 390, "height": 844}


def _capture(page, name: str, *, drawer: bool = True) -> None:
    """Save dark and light pictures at desktop and phone widths, when CAPTURE_1166_DIR is set."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        if drawer:
            page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", size is DESKTOP)
        for theme in ("dark", "light"):
            page.evaluate(
                "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
                theme,
            )
            page.wait_for_timeout(500)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"1166-{name}-{size_name}-{theme}.png"), full_page=True)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    page.set_viewport_size(DESKTOP)


def _make_guide() -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=GUIDE_EMAIL, email=GUIDE_EMAIL)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Gina Guide"
    member.save(update_fields=["status", "full_legal_name"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    return member


def _no_sideways_scroll(page) -> None:
    page.set_viewport_size(PHONE)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size(DESKTOP)


def _html(message) -> str:
    return next(body for body, kind in message.alternatives if kind == "text/html")


def describe_confirm_first():
    def it_confirms_one_request_and_declines_another(live_server, page, login_via_code):
        guide = _make_guide()
        day = timezone.localdate() + timedelta(days=3)
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        login_via_code(GUIDE_EMAIL)
        page.set_viewport_size(DESKTOP)

        # My Hours: Confirm Automatically is picked until the guide chooses Confirm First.
        page.goto(f"{live_server.url}{reverse('tours:hours')}")
        card = page.locator("[data-tour-confirm-mode]")
        assert card.locator('[data-tour-confirm-choice="automatic"]').is_checked()
        card.get_by_text("Confirm First", exact=True).click()
        if CAPTURE_DIR:
            page.set_viewport_size({"width": 1280, "height": 800})
            card.evaluate("(el) => el.scrollIntoView({block: 'center'})")
            shoot(page, Path(CAPTURE_DIR) / "step-1.png", target="tours.confirm-mode")
        with page.expect_navigation():
            card.locator("[data-tour-confirm-save]").click()
        page.locator(".plt-msg", has_text="Each new booking waits for you").first.wait_for()
        assert TourGuideSettings.confirms_first(guide)
        page.locator("[data-tour-confirm-mode]").scroll_into_view_if_needed()
        _capture(page, "setting")
        _no_sideways_scroll(page)

        mail.outbox = []
        sam = book_tour(guide=guide, starts_at=local(day, 16), visitor=Visitor("Sam Rivera", "sam@example.com", "", 2))
        lee = book_tour(guide=guide, starts_at=local(day, 17), visitor=Visitor("Lee Park", "lee@example.com", "", 1))
        assert {sam.status, lee.status} == {TourBooking.Status.PENDING}
        guide_email = next(m for m in mail.outbox if m.to == [GUIDE_EMAIL] and "Sam Rivera" in m.body)
        assert f"#tour-booking-{sam.pk}" in guide_email.body

        page.goto(f"{live_server.url}{reverse('tours:upcoming')}")
        row = page.locator(f'[data-tour-booking="{sam.pk}"]')
        row.locator('[data-tour-booking-status="pending"]').wait_for()
        if CAPTURE_DIR:
            shoot(page, Path(CAPTURE_DIR) / "step-2.png")
        _capture(page, "upcoming")
        _no_sideways_scroll(page)

        # Confirm Sam: booked, and the visitor gets the confirmation with the calendar invite.
        mail.outbox = []
        with page.expect_navigation():
            row.locator(f'[data-tour-confirm="{sam.pk}"]').click()
        page.locator(".plt-msg", has_text="Sam Rivera's tour on").first.wait_for()
        sam.refresh_from_db()
        assert sam.status == TourBooking.Status.BOOKED
        confirmed = next(m for m in mail.outbox if m.to == ["sam@example.com"])
        assert "confirmed" in confirmed.subject and confirmed.attachments

        # Decline Lee with a message: the seats are free again and the visitor is told why.
        mail.outbox = []
        page.locator(f'[data-tour-decline="{lee.pk}"]').click()
        dialog = page.get_by_role("dialog").filter(has_text="Decline This Request?")
        dialog.wait_for(state="visible")
        dialog.locator(f"#tour-decline-{lee.pk}-note").fill("I am away that evening, sorry.")
        with page.expect_navigation():
            dialog.get_by_role("button", name="Decline", exact=True).click()
        page.locator(".plt-msg", has_text="Lee Park's request for").first.wait_for()
        lee.refresh_from_db()
        assert lee.status == TourBooking.Status.CANCELLED
        assert any(m.to == ["lee@example.com"] and "I am away that evening" in m.body for m in mail.outbox)

        if CAPTURE_DIR:
            page.set_content(_html(guide_email))
            _capture(page, "guide-email", drawer=False)

    def it_tells_the_visitor_their_request_waits(live_server, page, settings):
        settings.TOURS_HOSTS = [urlparse(live_server.url).hostname]
        settings.TOURS_BASE_URL = live_server.url
        guide = _make_guide()
        TourGuideSettings.objects.create(guide=guide, confirm_mode=TourGuideSettings.ConfirmMode.FIRST)
        day = timezone.localdate() + timedelta(days=4)
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}/?date={day:%Y-%m-%d}")
        page.locator(".pl-tourpub__time").filter(has_text="4:00 PM").click()
        page.locator("[data-tour-form]").wait_for()
        page.locator('input[name="name"]').fill("Sam Rivera")
        page.locator('input[name="email"]').fill("sam@example.com")
        with page.expect_navigation():
            page.locator("[data-tour-form] button[type=submit]").click()
        page.locator("[data-tour-requested]").wait_for()
        assert page.locator("[data-tour-reschedule]").count() == 0
        page.locator("[data-tour-cancel]").wait_for()
        assert TourBooking.objects.get().status == TourBooking.Status.PENDING
        _capture(page, "visitor-pending", drawer=False)
        _no_sideways_scroll(page)
