"""End-to-end: a visitor picks No preference where two guides share a time, and the first guide to confirm gets it (#1255).

Alex and Gina both confirm first and both give a tour at 4:00 PM. On the tours host the visitor sees
a button for each guide and one for No preference, picks No preference and lands on Request Sent,
which names no guide. Alex's Upcoming tab shows the request with who else has it; Alex confirms it,
and Gina's copy is withdrawn. At a phone's width nothing scrolls sideways. ``CAPTURE_1255_DIR=<dir>``
saves the times and Upcoming at desktop width, dark and light, and the two guidance shots at the standard frame. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from membership.models import AdminCapability, Member
from tests.e2e.guidance import shoot
from tests.membership.factories import MembershipPlanFactory
from tests.tours.factories import TourHoursFactory
from tours.models import TourBooking, TourGuideSettings

CAPTURE_DIR = os.environ.get("CAPTURE_1255_DIR")
DESKTOP = {"width": 1100, "height": 900}
PHONE = {"width": 390, "height": 844}


def _guide(name: str, email: str) -> Member:
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["status", "full_legal_name"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    TourGuideSettings.objects.create(guide=member, confirm_mode=TourGuideSettings.ConfirmMode.FIRST)
    return member


def _capture(page, name: str) -> None:
    """Dark and light pictures at desktop width, when CAPTURE_1255_DIR is set."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    page.evaluate("() => document.querySelectorAll('.plt-toast').forEach((el) => el.remove())")
    for theme in ("dark", "light"):
        page.evaluate(
            "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
            theme,
        )
        page.wait_for_timeout(400)
        page.screenshot(path=str(Path(CAPTURE_DIR) / f"1255-{name}-{theme}.png"), full_page=True)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")


def _no_sideways_scroll(page) -> None:
    page.set_viewport_size(PHONE)
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size(DESKTOP)


def describe_no_preference():
    def it_sends_the_request_to_both_guides_and_the_first_to_confirm_gets_it(
        live_server, page, settings, login_via_code
    ):
        # The tours site answers on 127.0.0.1, the members site on localhost, both this live server.
        server = urlparse(live_server.url)
        tours_url = f"http://127.0.0.1:{server.port}"
        settings.TOURS_HOSTS = ["127.0.0.1"]
        settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "127.0.0.1"]
        settings.TOURS_BASE_URL = tours_url
        MembershipPlanFactory()  # so the user signal provisions each member
        alex = _guide("Alex Able", "tour-alex@example.com")
        gina = _guide("Gina Guide", "tour-gina@example.com")
        day = timezone.localdate() + timedelta(days=4)
        for member in (alex, gina):
            TourHoursFactory(guide=member, weekday=day.weekday(), seats=4)
        page.set_viewport_size(DESKTOP)

        page.goto(f"{tours_url}/?date={day:%Y-%m-%d}")
        times = page.locator(".pl-tourpub__time").filter(has_text="4:00 PM")
        assert times.count() == 3
        no_preference = page.locator("[data-tour-no-preference]").first
        assert "no preference" in no_preference.inner_text()
        if CAPTURE_DIR:
            shoot(page, Path(CAPTURE_DIR) / "step-1.png")
            page.set_viewport_size(DESKTOP)
        _capture(page, "times")
        _no_sideways_scroll(page)

        no_preference.click()
        page.locator("[data-tour-form]").wait_for()
        page.locator('input[name="name"]').fill("Sam Rivera")
        page.locator('input[name="email"]').fill("sam@example.com")
        with page.expect_navigation():
            page.locator("[data-tour-form] button[type=submit]").click()
        page.locator("[data-tour-requested]").wait_for()
        assert "every guide" in page.locator("[data-tour-requested]").inner_text()
        rows = list(TourBooking.objects.order_by("pk"))
        assert [row.guide for row in rows] == [alex, gina]

        page.context.clear_cookies()
        login_via_code("tour-alex@example.com")
        page.goto(f"{live_server.url}{reverse('tours:upcoming')}")
        row = page.locator(f'[data-tour-booking="{rows[0].pk}"]')
        row.locator("[data-tour-shared-with]").wait_for()
        assert "also sent to Gina" in row.inner_text()
        if CAPTURE_DIR:
            page.set_viewport_size({"width": 1280, "height": 800})
            row.evaluate("(el) => el.scrollIntoView({block: 'center'})")
            shoot(page, Path(CAPTURE_DIR) / "step-2.png")
            page.set_viewport_size(DESKTOP)
        _capture(page, "upcoming")
        _no_sideways_scroll(page)

        with page.expect_navigation():
            row.locator(f'[data-tour-confirm="{rows[0].pk}"]').click()
        page.locator(".plt-msg", has_text="Sam Rivera's tour on").first.wait_for()
        for booking in rows:
            booking.refresh_from_db()
        assert [booking.status for booking in rows] == [TourBooking.Status.BOOKED, TourBooking.Status.CANCELLED]
