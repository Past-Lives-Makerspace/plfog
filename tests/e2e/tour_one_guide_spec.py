"""End-to-end: each tour hour belongs to one guide, and visitors pick a time (#1309).

Dayzie opens My Hours, which has no Confirming Bookings card any more, adds Tuesday 5:00 to
7:00 PM, and the row says the hours are Amber's and does not save; 6:00 to 8:00 PM saves. An admin
sees Let guides share hours, off, on the Tours Settings tab. On the tours site, where Amber's and
Dayzie's hours overlapped before #1309, the day shows one tour per time and the form has no Guide
menu. At a phone's width nothing scrolls sideways. ``CAPTURE_1309_DIR=<dir>`` saves dark and light
pictures at desktop (1100) and phone (390) widths. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import time, timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from membership.models import AdminCapability, Member
from tests.membership.factories import MembershipPlanFactory
from tests.tours.factories import TourHoursFactory
from tours.models import TourHours

CAPTURE_DIR = os.environ.get("CAPTURE_1309_DIR")
DESKTOP = {"width": 1100, "height": 1000}
PHONE = {"width": 390, "height": 844}
DAYZIE = "tour-dayzie@example.com"
ADMIN = "tour-admin-1309@example.com"


def _capture(page, name: str, *, drawer: bool = True) -> None:
    """Dark and light pictures at desktop and phone widths, when CAPTURE_1309_DIR is set."""
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
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"1309-{name}-{size_name}-{theme}.png"), full_page=True)
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


def describe_one_guide_per_hour():
    def it_refuses_hours_another_guide_has_naming_them(live_server, page, login_via_code):
        MembershipPlanFactory()  # so the user signal provisions each member
        amber = _guide("tour-amber@example.com", "Amber Capwell")
        TourHoursFactory(guide=amber, weekday=TourHours.Weekday.TUESDAY, start_time=time(16), end_time=time(18))
        dayzie = _guide(DAYZIE, "Dayzie Dee")
        login_via_code(DAYZIE)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('tours:hours')}")
        page.locator("[data-tour-hours-empty]").wait_for()
        assert page.locator("[data-tour-confirm-mode]").count() == 0
        assert page.get_by_text("Confirming Bookings", exact=True).count() == 0

        page.locator("[data-tour-hours-add]").click()
        row = page.locator("[data-tour-hours-row]").first
        row.locator('select[name="hours-0-weekday"]').select_option("1")
        row.locator('select[name="hours-0-start_time"]').select_option("17:00")
        row.locator('select[name="hours-0-end_time"]').select_option("19:00")
        row.locator("[data-autosave-error]").get_by_text(
            "Every Tuesday, 4:00 PM to 6:00 PM is Amber's. Each tour hour belongs to one guide, so pick a time nobody has."
        ).wait_for()
        page.locator('[data-tour-save-status="hours"]').get_by_text("Not saved").wait_for()
        assert not TourHours.objects.filter(guide=dayzie).exists()
        _capture(page, "refused")
        _no_sideways_scroll(page)

        page.evaluate("() => { Alpine.$data(document.body).sidebarOpen = true; }")
        row.locator('select[name="hours-0-end_time"]').select_option("20:00")
        row.locator('select[name="hours-0-start_time"]').select_option("18:00")
        page.locator('[data-tour-save-status="hours"]').get_by_text("Saved", exact=True).wait_for()
        page.wait_for_function("() => document.querySelector('[data-guild-autosave]').plGuildAutosave.pending === 0")
        assert list(TourHours.objects.filter(guide=dayzie).values_list("start_time", "end_time")) == [
            (time(18), time(20))
        ]

    def it_shows_admins_the_switch_off(live_server, page, login_via_code):
        MembershipPlanFactory()
        _member(ADMIN, "Ada Admin", fog_role=Member.FogRole.ADMIN)
        login_via_code(ADMIN)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('tours:settings')}")
        switch = page.locator('[data-tour-settings-share] input[name="guides_share_hours"]')
        switch.wait_for(state="attached")
        assert not switch.is_checked()
        page.locator("[data-tour-settings-share]").scroll_into_view_if_needed()
        _capture(page, "settings")
        _no_sideways_scroll(page)

    def it_shows_visitors_one_tour_per_time_with_no_guide_menu(live_server, page, settings):
        settings.TOURS_HOSTS = [urlparse(live_server.url).hostname]
        settings.TOURS_BASE_URL = live_server.url
        MembershipPlanFactory()
        day = timezone.localdate() + timedelta(days=4)
        # Posted before #1309, when both guides could hold the same hours.
        for email, name in (("tour-amber@example.com", "Amber Capwell"), (DAYZIE, "Dayzie Dee")):
            TourHoursFactory(guide=_guide(email, name), weekday=day.weekday(), seats=4)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}/?date={day:%Y-%m-%d}")
        times = page.locator(".pl-tourpub__time")
        assert times.count() == 2
        assert page.locator("[data-tour-no-preference]").count() == 0
        _capture(page, "times", drawer=False)
        _no_sideways_scroll(page, drawer=False)
        with page.expect_navigation():
            times.filter(has_text="4:00 PM").click()
        page.locator("[data-tour-form]").wait_for()
        assert page.locator("[data-tour-guide]").count() == 0
        _capture(page, "form", drawer=False)
