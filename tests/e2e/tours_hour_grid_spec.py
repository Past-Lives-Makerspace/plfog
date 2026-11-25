"""End-to-end: the Tours Calendar tab's Week is an hour grid (#1332, part 1 of 2).

A guide opens the Calendar tab on this week: each guide's hours are a rail in their colour, tours
are cards placed at their time, three guides overlapping on one day still leave every card the
column's full width beside the rails, and a day nobody covers is striped with Add My Hours. A legend
chip hides that guide's rails and cards. Week draws no list under the grid; each card is the link to its
tour's row on Upcoming or Past, with a hover and keyboard focus state. On a phone one day shows at a time under a seven-day strip,
with no sideways scroll. Month still shows the shell's month grid. ``CAPTURE_1332_DIR=<dir>`` also
saves desktop (1280) and phone (390) pictures in both themes, and the two guidance shots under
``<dir>/guidance``. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import date, time, timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from membership.models import AdminCapability, Member, PermissionSetup
from tests.e2e.guidance import shoot
from tests.membership.factories import MembershipPlanFactory
from tests.tours.conftest import make_guide
from tests.tours.factories import TourBookingFactory, TourHoursFactory, local
from tours.models import FacilityTourSettings, TourBooking

GUIDE_EMAIL = "tour-guide-hour-grid@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_1332_DIR")
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
HOUR_PX = 64


def _set_drawer(page, *, is_open: bool) -> None:
    page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", is_open)
    page.wait_for_timeout(300)


def _theme(page, theme: str) -> None:
    page.evaluate(
        "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
        theme,
    )
    page.wait_for_timeout(400)


#: A full page picture paints the fixed top bar and sidebar where the viewport ended; pin them to the page instead.
_STILL_CHROME = (
    ".pl-topbar { position: static !important; } body { position: relative; }"
    " .hub-sidebar { position: absolute !important; top: 0; bottom: 0; height: auto !important; }"
    " .hub-feedback-fab { display: none !important; }"
)


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
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"1332-tours-{size_name}-{theme}.png"), full_page=True)
    _theme(page, "dark")
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def _make_guide() -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=GUIDE_EMAIL, email=GUIDE_EMAIL)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Robin Vale"
    member.save(update_fields=["status", "full_legal_name"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    return member


def _hours(guide: Member, day: date, start: time, end: time) -> None:
    TourHoursFactory(guide=guide, weekday=day.weekday(), date=day, start_time=start, end_time=end, seats=6)


def _tour(guide: Member | None, day: date, hour: int, name: str, party: int, minute: int = 0, **kwargs: object):
    return TourBookingFactory(
        guide=guide, starts_at=local(day, hour, minute), visitor_name=name, party_size=party, **kwargs
    )


def _seed_week(robin: Member) -> dict[str, object]:
    """The approved mockup's week, on this week's dates, with Wednesday left uncovered."""
    house = FacilityTourSettings.load()
    house.booking_horizon_days = 3  # a short Days With No Tour Hours card, so the pictures show the grid
    house.save()
    dana = make_guide(full_legal_name="Dana Ortiz")
    marcus = make_guide(full_legal_name="Marcus Tan")
    priya = make_guide(full_legal_name="Priya Shah")
    today = timezone.localdate()
    monday = today - timedelta(days=today.weekday())
    mon, tue, thu, fri, sat, sun = (monday + timedelta(days=offset) for offset in (0, 1, 3, 4, 5, 6))
    _hours(robin, mon, time(11), time(18))
    _hours(dana, mon, time(16), time(20))
    jordan = _tour(robin, mon, 13, "Jordan", 3)
    _tour(dana, mon, 17, "Ines", 2)
    _hours(marcus, tue, time(10), time(14))
    _hours(priya, tue, time(12), time(17))
    _tour(marcus, tue, 11, "Theo", 4)
    _tour(marcus, tue, 13, "Lee", 2, status=TourBooking.Status.PENDING)
    _tour(priya, tue, 15, "Ana", 3)
    _hours(dana, thu, time(18), time(21))
    _tour(dana, thu, 19, "Sol", 5)
    _hours(marcus, fri, time(8), time(10, 30))
    _tour(None, fri, 12, "Kai", 2)
    _hours(robin, fri, time(14), time(18))
    _tour(robin, fri, 15, "Mei", 4)
    _hours(robin, sat, time(10), time(14))
    _hours(dana, sat, time(11), time(15))
    _hours(priya, sat, time(12), time(16))
    _tour(robin, sat, 11, "Rae", 6)
    bo = _tour(dana, sat, 13, "Bo", 2)
    _tour(priya, sat, 14, "Uma", 3, minute=30)
    _hours(marcus, sun, time(12), time(16))
    _tour(marcus, sun, 13, "Noa", 3)
    return {"monday": monday, "jordan": jordan, "bo": bo, "dana": dana, "saturday": sat}


def _box(page, selector: str) -> dict[str, float]:
    return page.locator(selector).bounding_box()


def describe_tours_hour_grid():
    def it_lays_out_the_week_hour_by_hour(live_server, page, login_via_code):
        robin = _make_guide()
        seeded = _seed_week(robin)
        monday, saturday = seeded["monday"], seeded["saturday"]
        # The Get Set Up card is #1167's; hidden here so the pictures show this change.
        PermissionSetup.objects.update_or_create(
            member=robin, permission=AdminCapability.Capability.TOUR_GUIDE, defaults={"hidden_at": timezone.now()}
        )
        login_via_code(GUIDE_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('tours:calendar')}")
        page.evaluate("() => localStorage.removeItem('guildCalView')")
        page.reload()
        grid = page.locator("[data-tours-hour-grid]")
        grid.wait_for(state="visible")
        assert page.locator(".pl-hourgrid__col").count() == 7
        assert page.locator(".pl-calendar-grid--week").count() == 0
        # Week has no list under the grid: the cards are the links.
        assert page.locator(".pl-calendar-list:visible").count() == 0

        # The grid starts at 8 AM (Marcus's Friday hours) and runs to 9 PM.
        assert page.locator(".pl-hourgrid__time").first.inner_text() == "8 AM"
        assert page.locator(".pl-hourgrid__time").last.inner_text() == "8 PM"

        # Jordan's 1:00 PM tour sits five hours down Monday's column.
        monday_col = _box(page, f'[data-hourgrid-day="{monday.isoformat()}"]')
        jordan = page.locator(f'[data-hourgrid-card="{seeded["jordan"].pk}"]')
        assert "1:00 PM" in jordan.inner_text() and "Jordan (3)" in jordan.inner_text()
        assert abs(jordan.bounding_box()["y"] - (monday_col["y"] + 5 * HOUR_PX + 1)) <= 2

        # Saturday: three guides overlap, three rails side by side, every card the column's width beside them.
        sat_key = saturday.isoformat()
        assert page.locator(f'[data-hourgrid-day="{sat_key}"] .pl-hourgrid__rail').count() == 3
        sat_col = _box(page, f'[data-hourgrid-day="{sat_key}"]')
        rails_px = 4 + 9 * 3
        for card in page.locator(f'[data-hourgrid-day="{sat_key}"] .pl-hourgrid__card').all():
            box = card.bounding_box()
            assert box["width"] >= sat_col["width"] - rails_px - 12
            assert box["x"] >= sat_col["x"] + rails_px

        # Wednesday nobody covers: striped, with Add My Hours.
        wednesday = (monday + timedelta(days=2)).isoformat()
        assert "pl-hourgrid__col--uncovered" in page.locator(f'[data-hourgrid-day="{wednesday}"]').get_attribute(
            "class"
        )
        if timezone.localdate() <= monday + timedelta(days=2):
            page.locator(f'[data-hourgrid-day="{wednesday}"] [data-hourgrid-add]').wait_for()

        # A legend chip hides that guide's rails and cards, and brings them back.
        bo = page.locator(f'[data-hourgrid-card="{seeded["bo"].pk}"]')
        dana_chip = page.locator(".pl-calendar-filter", has_text="Dana Ortiz")
        dana_chip.click()
        bo.wait_for(state="hidden")
        dana_chip.click()
        bo.wait_for(state="visible")

        if CAPTURE_DIR:
            grid.scroll_into_view_if_needed()
            page.evaluate("() => window.scrollBy(0, -80)")
            shoot(page, Path(CAPTURE_DIR) / "guidance" / "step-1.png", target="tours.hour-grid")
            page.evaluate("() => document.querySelectorAll('style[data-guidance-ring]').forEach((s) => s.remove())")
            _capture(page, sat_key)
            page.reload()  # the pictures picked Saturday and pinned the chrome; a fresh page opens on today
            grid.wait_for(state="visible")

        # A phone: one day at a time, today first, picked from the strip; nothing scrolls sideways.
        page.set_viewport_size(PHONE)
        _set_drawer(page, is_open=False)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        today_key = timezone.localdate().isoformat()
        assert page.locator(f'[data-hourgrid-day-button="{today_key}"]').get_attribute("aria-pressed") == "true"
        assert page.locator(".pl-hourgrid__col:visible").count() == 1
        page.locator(f'[data-hourgrid-day-button="{sat_key}"]').click()
        page.locator(f'[data-hourgrid-day="{sat_key}"]').wait_for(state="visible")
        assert page.locator(".pl-hourgrid__col:visible").count() == 1
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        if CAPTURE_DIR:
            page.locator(".pl-hourgrid-days").scroll_into_view_if_needed()
            page.evaluate("() => window.scrollBy(0, -120)")
            shoot(page, Path(CAPTURE_DIR) / "guidance" / "step-2.png", frame="phone")
        page.set_viewport_size(DESKTOP)
        _set_drawer(page, is_open=True)

        # Month is the shell's month grid, and it still steps; Week comes back as the hour grid.
        page.get_by_role("button", name="Month", exact=True).click()
        page.locator(".pl-calendar-grid--month").wait_for(state="visible")
        grid.wait_for(state="hidden")
        assert page.locator(".pl-calendar-list:visible").count() == 1
        label = page.locator(".pl-calendar-nav__label:visible").inner_text()
        page.get_by_role("button", name="Next month").click()
        page.wait_for_function(
            "(old) => { const l = Array.from(document.querySelectorAll('.pl-calendar-nav__label')).find((e) => e.offsetParent !== null); return l && l.textContent.trim() !== old; }",
            arg=label,
        )
        page.locator(".pl-calendar-grid--month").wait_for(state="visible")
        page.get_by_role("button", name="Week", exact=True).click()
        page.locator("[data-tours-hour-grid]").wait_for(state="visible")
        assert page.locator(".pl-calendar-list:visible").count() == 0

        # A card is the link to its tour: it changes on hover, and Enter on it from the keyboard opens the tour's row.
        jordan_pk = seeded["jordan"].pk
        card = page.locator(f'[data-hourgrid-card="{jordan_pk}"]')
        resting = card.evaluate("(el) => getComputedStyle(el).backgroundColor")
        card.hover()
        page.wait_for_timeout(250)
        assert card.evaluate("(el) => getComputedStyle(el).backgroundColor") != resting
        page.mouse.move(0, 0)
        page.locator(".pl-calendar-nav__btn[aria-label='Next week']").focus()
        page.keyboard.press("Tab")  # the first card in the week, Monday's 1:00 PM
        assert card.evaluate("(el) => el === document.activeElement && el.matches(':focus-visible')")
        page.keyboard.press("Enter")
        page.wait_for_url(f"**#tour-booking-{jordan_pk}")
        page.locator(f'[data-tour-booking="{jordan_pk}"]').first.wait_for(state="visible")
