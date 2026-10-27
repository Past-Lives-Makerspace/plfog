"""End-to-end: cairn's front desk kiosk on the tours host (#950), check in or schedule a tour.

``live_server`` answers on ``localhost``, so the kiosk spec puts that host in ``TOURS_HOSTS`` (as on
tours.pastlives.space). Without the kiosk key ``/kiosk/`` is the public booking page with no names;
the kiosk link (``/kiosk/?key=``) sets the cookie and lands on the kiosk home. A visitor checks in
by email, by tapping their name in today's list, and by typing a name that matches two tours (the
pick list); a name that matches nothing shows the try again screen. Each check in stamps the
booking and rings the guide's bell. A tour is scheduled from the kiosk (source kiosk). The kiosk
returns home 60 seconds after the last input and a few seconds after a finished screen, and the
back button brings no visitor back. Rotating the key locks the screen out. Driven on Playwright's
fake clock, so no spec waits real seconds.

The Settings tab's kiosk link is the second spec (the members host, signed in as an admin).

``CAPTURE_950_DIR=<dir>`` saves every kiosk screen at cairn's 1920 by 1080 in both themes, and the
Settings tab's kiosk section at desktop (1100) and phone (390, sidebar drawer closed) widths in both
themes. Run with ``pytest -m e2e`` on PostgreSQL.
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

from core.models import Notification
from membership.models import AdminCapability, Member
from tests.membership.factories import MembershipPlanFactory
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.models import FacilityTourSettings, TourBooking

CAPTURE_DIR = os.environ.get("CAPTURE_950_DIR")
CAIRN = {"width": 1920, "height": 1080}
DESKTOP = {"width": 1100, "height": 1000}
PHONE = {"width": 390, "height": 844}
GUIDE_EMAIL = "tour-guide-kiosk@example.com"
ADMIN_EMAIL = "tour-admin-kiosk@example.com"
HOME = reverse("tours_kiosk:home")


def _capture(page, name: str, *, element: str = "") -> None:
    """The kiosk screen (or one ``element`` of it) at 1920 by 1080, dark and light, when CAPTURE_950_DIR is set."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for theme in ("dark", "light"):
        page.evaluate("(light) => window.__plSetTheme(light)", theme == "light")
        page.wait_for_timeout(400)
        path = str(Path(CAPTURE_DIR) / f"950-kiosk-{name}-{theme}.png")
        if element:
            page.locator(element).screenshot(path=path)
        else:
            page.screenshot(path=path, full_page=True)
    page.evaluate("() => window.__plSetTheme(true)")


def _capture_hub(page, name: str) -> None:
    """A hub page at desktop and phone widths (the drawer closed on a phone), dark and light."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        _set_drawer(page, is_open=size is DESKTOP)
        for theme in ("dark", "light"):
            page.evaluate(
                "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
                theme,
            )
            page.wait_for_timeout(600)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"950-{name}-{size_name}-{theme}.png"), full_page=True)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def _set_drawer(page, *, is_open: bool) -> None:
    page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", is_open)
    page.wait_for_timeout(300)


def _member(email: str, name: str, **fields: object) -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    for field, value in fields.items():
        setattr(member, field, value)
    member.save()
    return member


def _screen(page, name: str) -> None:
    expect(page.locator(f'[data-kiosk-screen="{name}"]')).to_be_visible()


def _hold(page) -> None:
    """Stop the page's clock where it is, so a finished screen stays up while it is checked and pictured.

    Python's ``pause_at`` reads a number as seconds since the epoch (``parse_time`` multiplies by
    1000), while ``Date.now()`` is milliseconds: passed raw, the clock jumped millennia ahead and
    fired the 8 second timer at once.
    """
    page.clock.pause_at(page.evaluate("() => Date.now()") / 1000 + 0.05)


def _goes_home_on_its_own(page, live_server, screen: str) -> None:
    """A finished screen stays up until its 8 second timer, then goes home by itself.

    The page loaded a moment before :func:`_hold`, so 6.5 seconds of page time is still inside the
    timer and 2 more are past it.
    """
    page.clock.run_for(6_500)
    _screen(page, screen)
    assert page.url != f"{live_server.url}{HOME}"
    page.clock.run_for(2_000)
    _at_home(page, live_server)
    page.clock.resume()


def _at_home(page, live_server) -> None:
    page.wait_for_url(f"{live_server.url}{HOME}")
    _screen(page, "home")


def _check_in_screen(page) -> None:
    page.locator('[data-kiosk-go="check-in"]').click()
    _screen(page, "check-in")


def _welcomed(page, live_server, first_name: str, guide_line: str) -> None:
    """The welcome, with the guide's name, then home a few seconds later on its own."""
    _screen(page, "welcome")
    _hold(page)
    expect(page.locator("[data-kiosk-welcome]")).to_have_text(f"Welcome, {first_name}!")
    expect(page.locator("[data-kiosk-guide]")).to_have_text(guide_line)


def describe_tours_kiosk():
    def it_checks_in_schedules_and_resets(live_server, page, settings):
        settings.TOURS_HOSTS = [urlparse(live_server.url).hostname]
        settings.TOURS_BASE_URL = live_server.url
        guide = _member(GUIDE_EMAIL, "Gina Guide")
        guide.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
        today = timezone.localdate()
        pat = TourBookingFactory(
            guide=guide, starts_at=local(today, 15), visitor_name="Pat Lee", visitor_email="pat.lee@example.com"
        )
        jo = TourBookingFactory(guide=guide, starts_at=local(today, 15, 30), visitor_name="Jo Tapper")
        sam = TourBookingFactory(guide=guide, starts_at=local(today, 16), visitor_name="Sam Rivera")
        ortiz = TourBookingFactory(guide=guide, starts_at=local(today, 17), visitor_name="Sam Ortiz", party_size=3)
        TourBookingFactory(
            guide=guide, starts_at=local(today, 18), visitor_name="Cy Cancelled", status=TourBooking.Status.CANCELLED
        )
        TourBookingFactory(guide=guide, starts_at=local(today + timedelta(days=1), 16), visitor_name="Tom Morrow")
        day = today + timedelta(days=4)
        TourHoursFactory(guide=guide, weekday=day.weekday(), seats=4)
        key = FacilityTourSettings.load().rotate_kiosk_key()
        page.set_viewport_size(CAIRN)
        page.clock.install()

        # No key: the public booking page, and no names.
        page.goto(f"{live_server.url}{HOME}")
        expect(page.locator('[data-tour-page="book"]')).to_be_visible()
        assert "Sam R." not in page.content() and "Pat L." not in page.content()

        # The kiosk link: the cookie, then the kiosk home without the key in the address.
        page.goto(f"{live_server.url}{HOME}?key={key}")
        _at_home(page, live_server)
        expect(page.locator(".cp-topbar__nav")).to_have_count(0)
        _capture(page, "home")

        # Check in: all three ways at once, today's booked tours only.
        _check_in_screen(page)
        names = page.locator("[data-kiosk-list]")
        for listed in ("Pat L.", "Jo T.", "Sam R.", "Sam O."):
            expect(names).to_contain_text(listed)
        expect(names).not_to_contain_text("Cy C.")
        expect(names).not_to_contain_text("Tom M.")
        expect(page.locator("#kiosk-email")).to_be_focused()
        _capture(page, "check-in")
        _capture(page, "list", element="[data-kiosk-list]")

        # By email, typed in any case: welcome, the guide's bell, then home on its own.
        page.locator("#kiosk-email").fill("PAT.Lee@Example.com")
        page.locator("#kiosk-email").press("Enter")
        _welcomed(page, live_server, "Pat", "Gina will be right with you.")
        _capture(page, "welcome")
        pat.refresh_from_db()
        assert pat.checked_in_at is not None and pat.status == TourBooking.Status.BOOKED
        bell = Notification.objects.get(user=guide.user, trigger="tour.checked_in")
        assert bell.title == "Your 3:00 tour is here"
        _goes_home_on_its_own(page, live_server, "welcome")
        # The back button brings back no visitor: the welcome was shown once, the form comes back empty.
        page.go_back()
        _screen(page, "check-in")
        expect(page.locator("#kiosk-email")).to_have_value("")
        expect(page.locator("[data-kiosk-welcome]")).to_have_count(0)
        page.locator("[data-kiosk-back]").click()
        _at_home(page, live_server)

        # By tapping a name.
        _check_in_screen(page)
        page.locator(f'[data-kiosk-name="{jo.pk}"]').click()
        _welcomed(page, live_server, "Jo", "Gina will be right with you.")
        _goes_home_on_its_own(page, live_server, "welcome")
        jo.refresh_from_db()
        assert jo.checked_in_at is not None

        # By typing a name two tours share: the pick list, then the right one.
        _check_in_screen(page)
        page.locator("#kiosk-name").fill("  sam ")
        page.locator("#kiosk-name").press("Enter")
        _screen(page, "pick")
        expect(page.locator("[data-kiosk-pick] [data-kiosk-name]")).to_have_count(2)
        _capture(page, "pick-list")
        page.locator(f'[data-kiosk-pick] [data-kiosk-name="{ortiz.pk}"]').click()
        _welcomed(page, live_server, "Sam", "Gina will be right with you.")
        _goes_home_on_its_own(page, live_server, "welcome")
        ortiz.refresh_from_db()
        sam.refresh_from_db()
        assert ortiz.checked_in_at is not None and sam.checked_in_at is None
        assert Notification.objects.filter(user=guide.user, trigger="tour.checked_in").count() == 3

        # A name nobody booked under today: try again, or schedule one.
        _check_in_screen(page)
        page.locator("#kiosk-name").fill("Tom Morrow")
        page.locator("#kiosk-name").press("Enter")
        _screen(page, "no-match")
        expect(page.locator('[data-kiosk-screen="no-match"] h1')).to_have_text("We Could Not Find a Tour Today")
        _capture(page, "no-match")
        page.locator("[data-kiosk-try-again]").click()
        _screen(page, "check-in")

        # Idle: half typed, then 60 seconds without input, home; the field comes back empty.
        page.locator("#kiosk-email").fill("half.typed@")
        page.clock.run_for(61_000)
        _at_home(page, live_server)
        _check_in_screen(page)
        expect(page.locator("#kiosk-email")).to_have_value("")

        # Schedule a tour: the website's date strip and times, the form, the confirmation.
        page.locator("[data-kiosk-back]").click()
        _at_home(page, live_server)
        page.locator('[data-kiosk-go="book"]').click()
        _screen(page, "book")
        page.locator(f'[data-tour-day="{day:%Y-%m-%d}"]').click()
        expect(page.locator(f'[data-tour-day="{day:%Y-%m-%d}"][aria-current="date"]')).to_be_visible()
        _capture(page, "schedule-picker")
        page.locator(".pl-tourpub__time").filter(has_text="4:00 PM").click()
        expect(page.locator("[data-tour-form]")).to_be_visible()
        expect(page.locator('[data-tour-account] input[name="create_account"]')).to_be_checked()
        page.locator('input[name="name"]').fill("Kim Kiosk")
        page.locator('input[name="email"]').fill("kim.kiosk@example.com")
        page.locator('select[name="party_size"]').select_option("2")
        _capture(page, "form")
        page.locator("[data-tour-form] button[type=submit]").click()
        _screen(page, "booked")
        _hold(page)
        expect(page.locator("[data-kiosk-booked]")).to_have_text("You're Booked, Kim!")
        _capture(page, "confirmation")
        kim = TourBooking.objects.get(visitor_email="kim.kiosk@example.com")
        assert kim.source == TourBooking.Source.KIOSK and kim.party_size == 2
        assert kim.starts_at == local(day, 16)
        assert kim.self_serve_token not in page.content()
        _goes_home_on_its_own(page, live_server, "booked")

        # Rotating the key locks this screen out: the public booking page again.
        FacilityTourSettings.load().rotate_kiosk_key()
        page.goto(f"{live_server.url}{HOME}")
        expect(page.locator('[data-tour-page="book"]')).to_be_visible()
        assert "Sam R." not in page.content()

    def it_shows_the_kiosk_link_on_the_settings_tab(live_server, page, login_via_code, settings):
        _member(ADMIN_EMAIL, "Ada Admin", fog_role=Member.FogRole.ADMIN)
        login_via_code(ADMIN_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('tours:settings')}")
        section = page.locator("[data-tour-settings-kiosk]")
        expect(section.locator("[data-tour-kiosk-create]")).to_be_visible()
        with page.expect_navigation():
            section.locator("[data-tour-kiosk-create]").click()
        key = FacilityTourSettings.load().kiosk_key
        assert key
        expect(page.locator("[data-tour-kiosk-url]")).to_have_value(f"{settings.TOURS_BASE_URL}{HOME}?key={key}")
        _capture_hub(page, "settings-kiosk")

        # Rotate asks first, then gives a new link.
        page.locator("[data-tour-kiosk-rotate]").click()
        dialog = page.locator('[role="dialog"]').filter(has_text="Rotate the Kiosk Key?")
        expect(dialog).to_be_visible()
        with page.expect_navigation():
            dialog.get_by_role("button", name="Rotate Key").click()
        rotated = FacilityTourSettings.load().kiosk_key
        assert rotated and rotated != key
        expect(page.locator("[data-tour-kiosk-url]")).to_have_value(f"{settings.TOURS_BASE_URL}{HOME}?key={rotated}")
