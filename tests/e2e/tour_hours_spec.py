"""End-to-end: a Tour Guide sets their weekly tour hours and a day off on the Tours page (#947).

The guide opens the Tools page from the sidebar (Tours has no sidebar entry of its own, Felix), opens
the Tours card (which lands on Upcoming) and its My Hours tab, adds Tuesday 4:00 to 6:00 PM with 1 hour tours and 4 seats
through + Add hours, saves, and sees the row folded to its summary after the reload. A second row
added and removed again never reaches the save. Delete asks first, then saves the page without the
row. A day off is added through the Add Day Off modal. At a phone's width nothing scrolls sideways.
``CAPTURE_947_DIR=<dir>`` also saves dark and light pictures at desktop and phone widths. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import time, timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from membership.models import AdminCapability, Member
from tests.membership.factories import MembershipPlanFactory
from tours.models import TourDayOff, TourHours

GUIDE_EMAIL = "tour-guide-hours@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_947_DIR")
DESKTOP = {"width": 1100, "height": 1000}
PHONE = {"width": 390, "height": 844}


def _capture(page, name: str) -> None:
    """Save dark and light pictures at desktop and phone widths, when CAPTURE_947_DIR is set."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        # The sidebar opened at desktop width stays open as a drawer over the page once narrowed;
        # a phone opens a page with it closed, so close it to show the page as a phone sees it.
        _set_drawer(page, is_open=size is DESKTOP)
        for theme in ("dark", "light"):
            page.evaluate(
                "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
                theme,
            )
            page.wait_for_timeout(600)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"947-{name}-{size_name}-{theme}.png"), full_page=True)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def _set_drawer(page, *, is_open: bool) -> None:
    """Open or close the hub sidebar (the body's Alpine ``sidebarOpen``) and let it settle."""
    page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", is_open)
    page.wait_for_timeout(300)


def _make_guide() -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=GUIDE_EMAIL, email=GUIDE_EMAIL)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = "Gina Guide"
    member.save(update_fields=["status", "full_legal_name"])
    member.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    return member


def describe_tour_hours_editor():
    def it_adds_saves_and_deletes_tour_hours_and_adds_a_day_off(live_server, page, login_via_code):
        guide = _make_guide()
        login_via_code(GUIDE_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('hub_home')}")
        assert page.locator('[data-nav="tours"]').count() == 0
        with page.expect_navigation():
            page.locator(f'.hub-sidebar__nav a[href="{reverse("hub_admin_tools")}"]').first.click()
        tours_card = page.locator("[data-tool-tours]")
        tours_card.wait_for()
        _capture(page, "tools-page")
        # The card is a boosted link: htmx swaps the body and pushes the URL.
        tours_card.click()
        # The card opens the Upcoming tab (#949); My Hours is the third tab.
        page.wait_for_url(f"**{reverse('tours:upcoming')}")
        page.locator('nav.pl-tabs [data-tours-tab="hours"]').click()
        page.wait_for_url(f"**{reverse('tours:hours')}")
        page.locator("[data-tour-hours-empty]").wait_for()
        page.locator("[data-tours-back]").wait_for()
        _capture(page, "empty")

        page.locator("[data-tour-hours-add]").click()
        page.locator("[data-tour-hours-empty]").wait_for(state="detached")
        row = page.locator("[data-tour-hours-row]").first
        row.locator("[data-tour-hours-body]").wait_for(state="visible")
        row.locator('select[name="hours-0-weekday"]').select_option("1")
        row.locator('select[name="hours-0-start_time"]').select_option("16:00")
        row.locator('select[name="hours-0-end_time"]').select_option("18:00")
        row.locator('select[name="hours-0-tour_minutes"]').select_option("60")
        row.locator('input[name="hours-0-seats"]').fill("4")
        row.locator('input[name="hours-0-seats"]').dispatch_event("change")
        row.locator("[data-tour-hours-summary]").get_by_text(
            "Tuesday · 4:00 PM to 6:00 PM · 1 hour each · 4 seats"
        ).wait_for()

        # A second row added and removed again is gone before the save.
        page.locator("[data-tour-hours-add]").click()
        page.locator("[data-tour-hours-row]").nth(1).locator("[data-tour-hours-remove]").click()
        assert page.locator("[data-tour-hours-row]").count() == 1

        with page.expect_navigation():
            page.locator("[data-tour-hours-save]").click()
        assert list(TourHours.objects.values_list("guide_id", "weekday", "seats")) == [(guide.pk, 1, 4)]

        saved = page.locator("[data-tour-hours-row]").first
        saved.locator("[data-tour-hours-summary]").get_by_text(
            "Tuesday · 4:00 PM to 6:00 PM · 1 hour each · 4 seats"
        ).wait_for()
        assert not saved.locator("[data-tour-hours-body]").is_visible()
        _capture(page, "saved")

        # Day off through the modal.
        page.locator("[data-tour-day-off-add]").click()
        day = timezone.localdate() + timedelta(days=5)
        page.locator('input[name="day_off-starts_on"]').fill(day.isoformat())
        _capture(page, "day-off-modal")
        with page.expect_navigation():
            page.locator("[data-tour-day-off-save]").click()
        page.locator("[data-tour-day-off]").first.wait_for()
        assert list(TourDayOff.objects.values_list("starts_on", "ends_on")) == [(day, day)]

        # At a phone's width nothing scrolls sideways.
        page.set_viewport_size(PHONE)
        _set_drawer(page, is_open=False)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        page.set_viewport_size(DESKTOP)
        _set_drawer(page, is_open=True)

        # Delete asks first, then saves the page without the row.
        saved = page.locator("[data-tour-hours-row]").first
        saved.locator("[data-tour-hours-edit]").click()
        saved.locator("[data-tour-hours-delete]").click()
        dialog = page.get_by_role("dialog").filter(has_text="Delete these tour hours?")
        dialog.wait_for(state="visible")
        assert TourHours.objects.exists()
        with page.expect_navigation():
            dialog.get_by_role("button", name="Delete", exact=True).click()
        page.locator("[data-tour-hours-empty]").wait_for()
        assert not TourHours.objects.exists()


#: ``CAPTURE_1160_DIR=<dir>`` saves the What's New shots (``step-1.png`` to ``step-3.png``, the
#: standard frame) and the PR's light theme and phone pictures of Available On (#1160).
CAPTURE_1160_DIR = os.environ.get("CAPTURE_1160_DIR")


def _center(page, selector: str) -> None:
    """Scroll ``selector`` to the middle of the viewport, so a viewport shot frames it."""
    page.locator(selector).first.evaluate("(el) => el.scrollIntoView({block: 'center'})")
    page.wait_for_timeout(200)


def _shoot_1160(page, name: str, selector: str, target: str) -> None:
    if not CAPTURE_1160_DIR:
        return
    from tests.e2e.guidance import shoot

    page.set_viewport_size({"width": 1280, "height": 800})
    _center(page, selector)
    shoot(page, Path(CAPTURE_1160_DIR) / name, target=target)


def _pictures_1160(page, name: str, selector: str) -> None:
    """The PR's pictures of one part of Available On: light theme at desktop, dark at a 390px phone."""
    if not CAPTURE_1160_DIR:
        return
    out = Path(CAPTURE_1160_DIR)
    page.evaluate("() => document.querySelectorAll('style[data-guidance-ring]').forEach((s) => s.remove())")
    page.set_viewport_size({"width": 1280, "height": 800})
    page.evaluate(
        "() => window.__plSetTheme ? window.__plSetTheme(true) : document.documentElement.setAttribute('data-theme', 'light')"
    )
    page.wait_for_timeout(600)
    page.locator(selector).first.screenshot(path=str(out / f"{name}-light.png"))
    page.set_viewport_size(PHONE)
    _set_drawer(page, is_open=False)
    page.evaluate(
        "() => window.__plSetTheme ? window.__plSetTheme(false) : document.documentElement.removeAttribute('data-theme')"
    )
    page.wait_for_timeout(600)
    page.locator(selector).first.screenshot(path=str(out / f"{name}-phone.png"))
    page.set_viewport_size(DESKTOP)
    _set_drawer(page, is_open=True)


def describe_available_on():
    def it_picks_dates_on_the_calendar_adds_them_and_deletes_one(live_server, page, login_via_code):
        guide = _make_guide()
        TourHours.objects.create(
            guide=guide,
            weekday=TourHours.Weekday.TUESDAY,
            start_time=time(16),
            end_time=time(18),
            tour_minutes=60,
            seats=4,
        )
        login_via_code(GUIDE_EMAIL)
        page.set_viewport_size(DESKTOP)
        # A boosted arrival: the calendar's component must already be registered from the head.
        page.goto(f"{live_server.url}{reverse('tours:upcoming')}")
        page.locator('nav.pl-tabs [data-tours-tab="hours"]').click()
        page.wait_for_url(f"**{reverse('tours:hours')}")
        page.locator("[data-tour-dates-empty]").wait_for()
        assert not page.locator("[data-tour-dates-panel]").is_visible()
        _shoot_1160(page, "step-1.png", "[data-tour-dates-add]", "tours.add-dates")
        page.set_viewport_size(DESKTOP)

        page.locator("[data-tour-dates-add]").click()
        page.locator("[data-tour-dates-panel]").wait_for(state="visible")
        today = timezone.localdate()
        assert page.locator("[data-tour-dates-month]").inner_text() == f"{today:%B %Y}"
        assert page.locator("[data-tour-dates-prev]").is_disabled()
        if today.day > 1:
            assert page.locator(f'[data-tour-date="{(today - timedelta(days=1)).isoformat()}"]').is_disabled()
        save = page.locator("[data-tour-dates-save]")
        assert save.is_disabled()

        # Next month is wholly ahead, so every day in it can be picked whatever today is.
        page.locator("[data-tour-dates-next]").click()
        first = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        assert page.locator("[data-tour-dates-month]").inner_text() == f"{first:%B %Y}"
        saturday = first + timedelta(days=(5 - first.weekday()) % 7)
        picks = [saturday, saturday + timedelta(days=1), saturday + timedelta(days=7)]
        for day in [*picks, picks[0]]:
            page.locator(f'[data-tour-date="{day.isoformat()}"]').click()
        assert page.locator("[data-tour-dates-count]").inner_text() == "2 dates picked"
        page.locator(f'[data-tour-date="{picks[0].isoformat()}"]').click()
        assert page.locator("[data-tour-dates-count]").inner_text() == "3 dates picked"
        assert save.inner_text() == "Add 3 Dates" and save.is_enabled()
        assert page.locator(f'[data-tour-date="{picks[0].isoformat()}"]').get_attribute("aria-pressed") == "true"

        panel = page.locator("[data-tour-dates-panel]")
        panel.locator('select[name="start_time"]').select_option("11:00")
        panel.locator('select[name="end_time"]').select_option("15:00")
        panel.locator('select[name="tour_minutes"]').select_option("60")
        panel.locator('input[name="seats"]').fill("6")
        _shoot_1160(page, "step-2.png", "[data-tour-dates-calendar]", "tours.dates-calendar")
        _pictures_1160(page, "1160-calendar", "[data-tour-dates]")

        # At a phone's width the grid fits with no sideways scroll and every day is a 40px target.
        page.set_viewport_size(PHONE)
        _set_drawer(page, is_open=False)
        assert page.evaluate("() => document.documentElement.scrollWidth <= window.innerWidth")
        sizes = page.locator(".pl-tours-cal__day:not(.pl-tours-cal__day--blank)").evaluate_all(
            "(days) => days.map((d) => [d.getBoundingClientRect().width, d.getBoundingClientRect().height])"
        )
        assert sizes and min(min(width, height) for width, height in sizes) >= 40, sizes
        page.set_viewport_size(DESKTOP)
        _set_drawer(page, is_open=True)

        with page.expect_navigation():
            save.click()
        page.locator("text=Added 3 dates. >> visible=true").first.wait_for()
        assert sorted(TourHours.objects.dated().values_list("date", flat=True)) == picks
        rows = page.locator("[data-tour-dated]")
        assert rows.count() == 3
        assert rows.first.inner_text().startswith(
            f"{saturday:%a, %b} {saturday.day} · 11:00 AM to 3:00 PM · 60 minute tours · 6 seats"
        )
        assert not page.locator("[data-tour-dates-panel]").is_visible()
        assert page.locator("[data-tour-hours-row]").count() == 1
        _shoot_1160(page, "step-3.png", "[data-tour-dates-list]", "tours.available-on")
        _pictures_1160(page, "1160-list", "[data-tour-dates]")

        # Delete asks first.
        rows.first.locator("[data-tour-dated-delete]").click()
        dialog = page.get_by_role("dialog").filter(has_text="Delete these hours?")
        dialog.wait_for(state="visible")
        with page.expect_navigation():
            dialog.get_by_role("button", name="Delete", exact=True).click()
        page.locator("text=Hours deleted. >> visible=true").first.wait_for()
        assert page.locator("[data-tour-dated]").count() == 2
        assert TourHours.objects.dated().count() == 2

    def it_reopens_the_panel_with_the_picks_after_a_refused_save(live_server, page, login_via_code):
        guide = _make_guide()
        login_via_code(GUIDE_EMAIL)
        day = timezone.localdate() + timedelta(days=3)
        TourDayOff.objects.create(guide=guide, starts_on=day, ends_on=day)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('tours:hours')}")
        page.locator("[data-tour-dates-add]").click()
        cell = page.locator(f'[data-tour-date="{day.isoformat()}"]')
        if not cell.count():
            page.locator("[data-tour-dates-next]").click()
        assert "pl-tours-cal__day--off" in (cell.get_attribute("class") or "")
        cell.click()
        page.locator('[data-tour-dates-panel] select[name="start_time"]').select_option("11:00")
        page.locator('[data-tour-dates-panel] select[name="end_time"]').select_option("15:00")
        with page.expect_navigation():
            page.locator("[data-tour-dates-save]").click()
        page.locator("[data-tour-dates-panel]").wait_for(state="visible")
        assert page.locator("[data-tour-dates-error]").inner_text().endswith("is a day off. Delete the day off first.")
        assert page.locator("[data-tour-dates-count]").inner_text() == "1 date picked"
        assert page.locator('[data-tour-dates-panel] select[name="start_time"]').input_value() == "11:00"
        assert not TourHours.objects.exists()
        page.locator("[data-tour-dates-cancel]").click()
        page.locator("[data-tour-dates-panel]").wait_for(state="hidden")
        page.locator("[data-tour-dates-add]").click()
        assert page.locator("[data-tour-dates-count]").inner_text() == "0 dates picked"
