"""End-to-end: guides' hours may overlap, one tour runs at a time, each guide sets their notice (#1309, #1312).

Dayzie opens My Hours, which has no Confirming Bookings card, adds Tuesday 4:30 to 6:30 PM over
Amber's 4:00 to 6:00 PM, and it saves; she then picks 15 minutes under Booking Notice, which saves
itself. On the tours site, where Amber and Dayzie give the same hours, the day shows one tour per
time with no guide menu; Sam books 4:00 PM. A second visitor sees the day marked on the calendar
and 4:00 PM first, "2 people going, join them", books it and joins Sam on the same guide's tour.
The kiosk's times name that guide. At a phone's width nothing scrolls sideways.
``CAPTURE_1312_DIR=<dir>`` saves dark and light pictures at desktop (1100) and phone (390) widths,
and the two guidance shots at the standard frame. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import time, timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import AdminCapability, Member
from tests.e2e.guidance import shoot
from tests.membership.factories import MembershipPlanFactory
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.models import FacilityTourSettings, TourBooking, TourGuideSettings, TourHours

CAPTURE_DIR = os.environ.get("CAPTURE_1312_DIR")
DESKTOP = {"width": 1100, "height": 1000}
PHONE = {"width": 390, "height": 844}
DAYZIE = "tour-dayzie@example.com"
SAM = "sam-1312@example.com"


def _capture(
    page, name: str, *, drawer: bool = True, sizes: tuple[str, ...] = ("desktop", "phone"), element: str = ""
) -> None:
    """Dark and light pictures at desktop and phone widths, when CAPTURE_1312_DIR is set."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        if size_name not in sizes:
            continue
        page.set_viewport_size(size)
        if drawer:
            page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", size is DESKTOP)
        for theme in ("dark", "light"):
            page.evaluate(
                "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
                theme,
            )
            page.wait_for_timeout(500)
            out = str(Path(CAPTURE_DIR) / f"1312-{name}-{size_name}-{theme}.png")
            if element:
                page.locator(element).screenshot(path=out)
            else:
                page.screenshot(path=out, full_page=True)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    page.set_viewport_size(DESKTOP)
    if drawer:
        page.evaluate("() => { Alpine.$data(document.body).sidebarOpen = true; }")


def _no_sideways_scroll(page, *, drawer: bool = True) -> None:
    page.set_viewport_size(PHONE)
    if drawer:
        page.evaluate("() => { Alpine.$data(document.body).sidebarOpen = false; }")
        page.wait_for_timeout(300)
    assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
    page.set_viewport_size(DESKTOP)


def _member(email: str, name: str, **extra: object) -> Member:
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    for field, value in extra.items():
        setattr(member, field, value)
    member.save()
    return member


def _guide(email: str, name: str) -> Member:
    member = _member(email, name)
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    return member


def _book(page, *, email: str, party: str) -> None:
    page.locator('input[name="name"]').fill("Sam Rivera")
    page.locator('input[name="email"]').fill(email)
    page.locator('input[name="phone"]').fill("503 555 0142")
    page.locator('select[name="party_size"]').select_option(party)
    with page.expect_navigation():
        page.get_by_role("button", name="Book My Tour").click()
    page.wait_for_url("**/b/**")


def describe_one_tour_at_a_time():
    def it_saves_overlapping_hours_and_a_15_minute_notice(live_server, page, login_via_code):
        MembershipPlanFactory()  # so the user signal provisions each member
        amber = _guide("tour-amber@example.com", "Amber Capwell")
        TourHoursFactory(guide=amber, weekday=TourHours.Weekday.TUESDAY, start_time=time(16), end_time=time(18))
        dayzie = _guide(DAYZIE, "Dayzie Dee")
        login_via_code(DAYZIE)
        page.set_viewport_size(DESKTOP)
        # Hours are set on My Hours since #1300.
        page.goto(f"{live_server.url}{reverse('hours:my_hours')}")
        page.locator("[data-hours-weekly-empty]").wait_for()
        assert page.locator("[data-tour-confirm-mode]").count() == 0

        page.locator("[data-hours-add]").click()
        row = page.locator("[data-hours-weekly] [data-hours-set]").first
        row.locator('select[name="weekly-0-weekday"]').select_option("1")
        row.locator('select[name="weekly-0-start_time"]').select_option("16:30")
        row.locator('select[name="weekly-0-end_time"]').select_option("18:30")
        page.locator('[data-tour-save-status="weekly"]').get_by_text("Saved", exact=True).wait_for()
        page.wait_for_function("() => document.querySelector('[data-guild-autosave]').plGuildAutosave.pending === 0")
        assert list(TourHours.objects.filter(guide=dayzie).values_list("start_time", "end_time")) == [
            (time(16, 30), time(18, 30))
        ]

        notice = page.locator("[data-tour-notice]")
        expect(notice).to_have_value("")
        notice.select_option(label="15 minutes")
        page.locator('[data-tour-save-status="notice"]').get_by_text("Saved", exact=True).wait_for()
        assert TourGuideSettings.objects.get(guide=dayzie).min_notice_minutes == 15
        page.locator("[data-tour-notice-card]").scroll_into_view_if_needed()
        if CAPTURE_DIR:
            page.set_viewport_size({"width": 1280, "height": 800})
            page.evaluate("() => document.querySelector('[data-tour-notice-card]').scrollIntoView({block: 'center'})")
            shoot(page, Path(CAPTURE_DIR) / "guidance" / "step-1.png", target="tours.notice")
            page.set_viewport_size(DESKTOP)
            page.evaluate("() => { Alpine.$data(document.body).sidebarOpen = true; }")
            page.evaluate("() => document.querySelectorAll('style[data-guidance-ring]').forEach((s) => s.remove())")
        _capture(page, "notice", element="[data-tour-notice-card]")
        _no_sideways_scroll(page)

    def it_books_a_shared_time_and_the_next_visitor_joins_the_same_guide(live_server, page, settings):
        settings.TOURS_HOSTS = [urlparse(live_server.url).hostname]
        settings.TOURS_BASE_URL = live_server.url
        MembershipPlanFactory()
        day = timezone.localdate() + timedelta(days=4)
        amber = _guide("tour-amber@example.com", "Amber Capwell")
        dayzie = _guide(DAYZIE, "Dayzie Dee")
        for guide in (amber, dayzie):
            TourHoursFactory(guide=guide, weekday=day.weekday(), seats=6)
        # A week on, someone is already going: that day carries the green dot while another is picked.
        TourBookingFactory(guide=amber, starts_at=local(day + timedelta(days=7), 16), party_size=1)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}/?date={day:%Y-%m-%d}")
        times = page.locator(".pl-tourpub__time")
        expect(times).to_have_count(2)
        assert page.locator(f'[data-tour-day="{day:%Y-%m-%d}"][data-tour-going-day]').count() == 0
        with page.expect_navigation():
            times.filter(has_text="4:00 PM").click()
        page.locator("[data-tour-form]").wait_for()
        assert page.locator("[data-tour-guide]").count() == 0
        _book(page, email=SAM, party="2")
        first = TourBooking.objects.get(visitor_email=SAM)

        # A second visitor: the day is marked, and 4:00 PM comes first, saying who is going (never their names).
        page.context.clear_cookies()
        page.goto(f"{live_server.url}/?date={day:%Y-%m-%d}")
        expect(page.locator(f'[data-tour-day="{day:%Y-%m-%d}"][data-tour-going-day]')).to_have_count(1)
        expect(page.locator("[data-tour-going-day]")).to_have_count(2)
        expect(page.locator("[data-tour-going-key]")).to_be_visible()
        joined = page.locator(".pl-tourpub__time").first
        expect(joined).to_contain_text("4:00 PM")
        expect(joined).to_contain_text("2 people going, join them")
        assert page.get_by_text("Sam Rivera").count() == 0
        if CAPTURE_DIR:
            shoot(page, Path(CAPTURE_DIR) / "guidance" / "step-2.png")
            page.set_viewport_size(DESKTOP)
        _capture(page, "calendar", drawer=False)
        _no_sideways_scroll(page, drawer=False)
        with page.expect_navigation():
            joined.click()
        page.locator("[data-tour-form]").wait_for()
        _book(page, email="kim-1312@example.com", party="3")
        second = TourBooking.objects.get(visitor_email="kim-1312@example.com")
        assert (second.guide_id, second.starts_at) == (first.guide_id, first.starts_at)

        # The kiosk names the guide who has the tour.
        key = FacilityTourSettings.load().rotate_kiosk_key()
        page.goto(f"{live_server.url}{reverse('tours_kiosk:home')}?key={key}")
        page.goto(f"{live_server.url}{reverse('tours_kiosk:book')}?date={day:%Y-%m-%d}")
        kiosk_first = page.locator(".pl-tourpub__time").first
        expect(kiosk_first).to_contain_text("5 people going, join them")
        expect(kiosk_first).to_contain_text(f"with {first.guide.greeting_name.split()[0]}")
        _capture(page, "kiosk", drawer=False, sizes=("desktop",))
