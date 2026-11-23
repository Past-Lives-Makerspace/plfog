"""End-to-end: one button per time, then a Guide dropdown where two guides share it, and the first guide to confirm gets a No preference request (#1255).

Alex and Gina both confirm first and both give a tour at 4:00 PM (Gina with 2 seats). On the tours
host the visitor sees one 4:00 PM button, picks it, and the form offers the Guide dropdown with No
preference picked; a party too big for Gina greys her out. With No preference the visitor lands on
Request Sent, which names no guide. Alex's Upcoming tab shows the request with who else has it;
Alex confirms it, and Gina's copy is withdrawn. Picking Gina in the dropdown asks Gina alone. At a
phone's width nothing scrolls sideways. ``CAPTURE_1255_DIR=<dir>`` saves the times and the form at
phone width (dark and light) and desktop light, Upcoming at desktop, and the two guidance shots at
the standard frame. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.contrib.auth.models import User
from playwright.sync_api import expect
from django.urls import reverse
from django.utils import timezone

from membership.models import AdminCapability, Member
from tests.e2e.guidance import shoot
from tests.membership.factories import MembershipPlanFactory
from tests.tours.conftest import let_guides_share_hours
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


def _theme(page, theme: str) -> None:
    page.evaluate(
        "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
        theme,
    )
    page.wait_for_timeout(400)


def _capture(page, name: str) -> None:
    """Phone dark and light and desktop light, when CAPTURE_1255_DIR is set; back to desktop after."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    page.evaluate("() => document.querySelectorAll('.plt-toast').forEach((el) => el.remove())")
    for viewport, label, themes in ((PHONE, "phone", ("dark", "light")), (DESKTOP, "desktop", ("light",))):
        page.set_viewport_size(viewport)
        for theme in themes:
            _theme(page, theme)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"1255-{name}-{label}-{theme}.png"), full_page=True)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    page.set_viewport_size(DESKTOP)


def _tours_site(live_server, settings) -> str:
    """The tours site answers on 127.0.0.1, the members site on localhost, both this live server."""
    server = urlparse(live_server.url)
    tours_url = f"http://127.0.0.1:{server.port}"
    settings.TOURS_HOSTS = ["127.0.0.1"]
    settings.ALLOWED_HOSTS = [*settings.ALLOWED_HOSTS, "127.0.0.1"]
    settings.TOURS_BASE_URL = tours_url
    return tours_url


def _alex_and_gina() -> tuple[Member, Member, date]:
    MembershipPlanFactory()  # so the user signal provisions each member
    alex = _guide("Alex Able", "tour-alex@example.com")
    gina = _guide("Gina Guide", "tour-gina@example.com")
    day = timezone.localdate() + timedelta(days=4)
    TourHoursFactory(guide=alex, weekday=day.weekday(), seats=4)
    TourHoursFactory(guide=gina, weekday=day.weekday(), seats=2)
    let_guides_share_hours()  # two guides at one time need Tour Settings' switch on (#1309)
    return alex, gina, day


def _pick_four(page, tours_url: str, day: date):
    """Open the day, check 4:00 PM is one button, pick it and wait for the Guide dropdown."""
    page.goto(f"{tours_url}/?date={day:%Y-%m-%d}")
    times = page.locator(".pl-tourpub__time").filter(has_text="4:00 PM")
    expect(times).to_have_count(1)
    expect(times.locator(".pl-tourpub__time-seats")).to_have_text("4 seats left")
    return times


def _no_sideways_scroll(page) -> None:
    page.set_viewport_size(PHONE)
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size(DESKTOP)


def describe_no_preference():
    def it_shows_one_time_then_the_guide_dropdown_and_the_first_to_confirm_gets_it(
        live_server, page, settings, login_via_code
    ):
        tours_url = _tours_site(live_server, settings)
        alex, gina, day = _alex_and_gina()
        page.set_viewport_size(DESKTOP)

        times = _pick_four(page, tours_url, day)
        _capture(page, "times")
        _no_sideways_scroll(page)

        times.click()
        guide = page.locator("[data-tour-guide]")
        expect(guide).to_be_visible()
        options = guide.locator("option")
        expect(options).to_have_text(["No preference", "Alex (4 seats left)", "Gina (2 seats left)"])
        assert guide.evaluate("(el) => el.options[el.selectedIndex].text") == "No preference"
        if CAPTURE_DIR:
            shoot(page, Path(CAPTURE_DIR) / "step-1.png")
            page.set_viewport_size(DESKTOP)
        _capture(page, "guide")
        _no_sideways_scroll(page)

        # Gina, then a party of 3: Gina has 2 seats, so she greys out and the pick goes back to No preference.
        guide.select_option(label="Gina (2 seats left)")
        expect(page.locator("[data-tour-seats]")).to_have_text("2 seats left")
        page.locator('select[name="party_size"]').select_option("3")
        expect(options.nth(2)).to_be_disabled()
        expect(guide).to_have_value(options.nth(0).get_attribute("value"))
        expect(page.locator("[data-tour-seats]")).to_have_text("4 seats left")
        page.locator('select[name="party_size"]').select_option("1")
        expect(options.nth(2)).to_be_enabled()

        page.locator('input[name="name"]').fill("Sam Rivera")
        page.locator('input[name="email"]').fill("sam@example.com")
        page.locator('input[name="phone"]').fill("503 555 0142")
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
        _no_sideways_scroll(page)

        page.locator(f'[data-tour-booking="{rows[0].pk}"] .pl-row-menu__trigger').click()
        with page.expect_navigation():
            row.locator(f'[data-tour-confirm="{rows[0].pk}"]').click()
        page.locator(".plt-msg", has_text="Sam Rivera's tour on").first.wait_for()
        for booking in rows:
            booking.refresh_from_db()
        assert [booking.status for booking in rows] == [TourBooking.Status.BOOKED, TourBooking.Status.CANCELLED]

    def it_asks_only_the_guide_picked_in_the_dropdown(live_server, page, settings):
        tours_url = _tours_site(live_server, settings)
        _, gina, day = _alex_and_gina()
        page.set_viewport_size(DESKTOP)
        _pick_four(page, tours_url, day).click()
        page.locator("[data-tour-guide]").select_option(label="Gina (2 seats left)")
        page.locator('input[name="name"]').fill("Sam Rivera")
        page.locator('input[name="email"]').fill("sam@example.com")
        page.locator('input[name="phone"]').fill("503 555 0142")
        with page.expect_navigation():
            page.locator("[data-tour-form] button[type=submit]").click()
        page.locator("[data-tour-requested]").wait_for()
        booking = TourBooking.objects.get()
        assert (booking.guide, booking.status, booking.request_group) == (gina, TourBooking.Status.PENDING, None)
