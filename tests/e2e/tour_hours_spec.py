"""End-to-end: a Tour Guide sets their weekly tour hours and a day off on the Tours page (#947).

The guide opens the Tools page from the sidebar (Tours has no sidebar entry of its own, Felix), opens
the Tours card, adds Tuesday 4:00 to 6:00 PM with 1 hour tours and 4 seats
through + Add hours, saves, and sees the row folded to its summary after the reload. A second row
added and removed again never reaches the save. Delete asks first, then saves the page without the
row. A day off is added through the Add Day Off modal. At a phone's width nothing scrolls sideways.
``CAPTURE_947_DIR=<dir>`` also saves dark and light pictures at desktop and phone widths. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import timedelta
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
