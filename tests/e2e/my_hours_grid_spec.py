"""End-to-end: My Hours' This Week is an hour grid (#1332, part 2 of 2).

Robin's week from the approved mockup (``mockups/1300-hour-grid-week.html?view=myhours``, seeded by
``tests.e2e.my_hours_spec._seed``): open hours dashed and placed by their times with the bookings
inside them, the day off striped. A class taught over open hours sits beside them, never on top. On a
phone one day shows at a time under the seven-day strip, today first, with no sideways scroll.
``CAPTURE_1332_DIR=<dir>`` also saves desktop (1280x800) and phone (390) pictures in both themes.
Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from django.urls import reverse
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from membership.models import AdminCapability, PermissionSetup
from tests.e2e.my_hours_spec import EMAIL, _at, _robin, _seed

CAPTURE_DIR = os.environ.get("CAPTURE_1332_DIR")
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
HOUR_PX = 64

#: A full page picture paints the fixed top bar and sidebar where the viewport ended; pin them to the page instead.
_STILL_CHROME = (
    ".pl-topbar { position: static !important; } body { position: relative; }"
    " .hub-sidebar { position: absolute !important; top: 0; bottom: 0; height: auto !important; }"
    " .hub-feedback-fab { display: none !important; }"
)


def _set_drawer(page, *, is_open: bool) -> None:
    page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", is_open)
    page.wait_for_timeout(300)


def _theme(page, theme: str) -> None:
    page.evaluate(
        "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light')"
        " : document.documentElement.removeAttribute('data-theme')",
        theme,
    )
    page.wait_for_timeout(400)


def _capture(page, phone_day: str) -> None:
    """Full page pictures at 1280 and 390 wide, dark and light, when CAPTURE_1332_DIR is set; the phone shows ``phone_day``."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    page.add_style_tag(content=_STILL_CHROME)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        _set_drawer(page, is_open=size is DESKTOP)
        if size is PHONE:
            page.locator(f'[data-hourgrid-day-button="{phone_day}"]').click()
        for theme in ("dark", "light"):
            _theme(page, theme)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"1332-myhours-{size_name}-{theme}.png"), full_page=True)
    _theme(page, "dark")
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def _no_overlap(a: dict[str, float], b: dict[str, float]) -> bool:
    return a["x"] + a["width"] <= b["x"] or b["x"] + b["width"] <= a["x"]


def describe_my_hours_hour_grid():
    def it_lays_the_week_out_hour_by_hour(live_server, page, login_via_code, settings):
        settings.GOOGLE_OAUTH_CLIENT_ID = "client-id"
        settings.GOOGLE_OAUTH_CLIENT_SECRET = "client-secret"
        robin = _robin()
        today = timezone.localdate()
        monday = today - timedelta(days=today.weekday())
        _seed(robin, monday)
        PermissionSetup.objects.update_or_create(
            member=robin, permission=AdminCapability.Capability.TOUR_GUIDE, defaults={"hidden_at": timezone.now()}
        )
        login_via_code(EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('hours:my_hours')}")
        week = page.locator("[data-hours-week] [data-hourgrid]")
        week.wait_for(state="visible")
        assert page.locator(".pl-hourgrid__col").count() == 7
        assert page.locator(".pl-hourgrid__time").first.inner_text() == "9 AM"
        assert page.locator(".pl-hourgrid__time").last.inner_text() == "8 PM"

        # Monday: open 11 AM to 6 PM, two hours down; the 1:00 PM tour inside it, four hours down.
        mon_key = monday.isoformat()
        column = page.locator(f'[data-hourgrid-day="{mon_key}"]').bounding_box()
        open_box = page.locator(f'[data-hourgrid-day="{mon_key}"] [data-hours-block="open"]').bounding_box()
        assert abs(open_box["y"] - (column["y"] + 2 * HOUR_PX + 1)) <= 2
        assert abs(open_box["height"] - (7 * HOUR_PX - 3)) <= 2
        tour = page.locator(f'[data-hourgrid-day="{mon_key}"] .pl-hourgrid__inner').first
        tour_box = tour.bounding_box()
        assert abs(tour_box["y"] - (column["y"] + 4 * HOUR_PX + 1)) <= 4
        assert open_box["x"] < tour_box["x"] and tour_box["x"] + tour_box["width"] < open_box["x"] + open_box["width"]

        # Tuesday is the day off: striped, Day Off at the top.
        tue = page.locator(f'[data-hourgrid-day="{(monday + timedelta(days=1)).isoformat()}"]')
        assert "pl-hourgrid__col--off" in tue.get_attribute("class")
        assert "repeating-linear-gradient" in tue.evaluate("(el) => getComputedStyle(el).backgroundImage")

        _capture(page, mon_key)

        # A class over Saturday's open hours sits beside them, each half the column, never on top.
        saturday = monday + timedelta(days=5)
        linocut = ClassOfferingFactory(instructors=[robin], title="Wood Type", status=ClassOffering.Status.PUBLISHED)
        ClassSessionFactory(class_offering=linocut, starts_at=_at(saturday, 12), ends_at=_at(saturday, 14))
        page.goto(f"{live_server.url}{reverse('hours:my_hours')}")
        sat = page.locator(f'[data-hourgrid-day="{saturday.isoformat()}"]')
        sat.wait_for(state="visible")
        sat_box = sat.bounding_box()
        open_sat = sat.locator('[data-hours-block="open"]').bounding_box()
        busy_sat = sat.locator('[data-hours-block="busy"]').bounding_box()
        assert _no_overlap(open_sat, busy_sat)
        assert open_sat["width"] < sat_box["width"] / 2 and busy_sat["width"] < sat_box["width"] / 2

        # A phone: one day at a time, today first, picked from the strip; nothing scrolls sideways.
        page.set_viewport_size(PHONE)
        _set_drawer(page, is_open=False)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        today_key = today.isoformat()
        assert page.locator(f'[data-hourgrid-day-button="{today_key}"]').get_attribute("aria-pressed") == "true"
        assert page.locator(".pl-hourgrid__col:visible").count() == 1
        page.locator(f'[data-hourgrid-day-button="{mon_key}"]').click()
        page.locator(f'[data-hourgrid-day="{mon_key}"]').wait_for(state="visible")
        assert page.locator(".pl-hourgrid__col:visible").count() == 1
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
