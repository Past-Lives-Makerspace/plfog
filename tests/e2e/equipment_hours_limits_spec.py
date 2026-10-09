"""End-to-end: the equipment Hours & Limits tab reads clearly (#731).

A manager opens the tab and finds each opening hours window folded to one summary line, opens one
with Edit, turns the Availability card's Active switch off, sees the closed message field appear,
and saves: the equipment is closed with that message. ``CAPTURE_731_DIR=<dir>`` also saves dark and
light pictures of the Hours & Limits and Reservations tabs there. Run with ``pytest -m e2e`` on
PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse

from core.models import SiteConfiguration
from membership.models import Member
from tests.membership.factories import EquipmentFactory, EquipmentHoursFactory, MembershipPlanFactory

MANAGER_EMAIL = "equipment-hours-limits@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_731_DIR")


def _capture(page, name: str, hover=None) -> None:
    """Save a dark and a light picture of the page as it stands, when CAPTURE_731_DIR is set.

    Dark is the hub's default (no data-theme attribute); the pause lets the theme's colour
    transitions finish, and ``hover`` (a locator) is hovered again after the resize so its
    tooltip shows in the picture.
    """
    if not CAPTURE_DIR:
        return
    page.set_viewport_size({"width": 1100, "height": 1900})
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for theme in ("dark", "light"):
        page.evaluate(
            "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
            theme,
        )
        page.wait_for_timeout(600)
        if hover is not None:
            hover.hover()
            page.wait_for_timeout(300)
        page.screenshot(path=str(Path(CAPTURE_DIR) / f"731-{name}-{theme}.png"))
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    page.set_viewport_size({"width": 1100, "height": 900})


def describe_equipment_hours_and_limits_tab():
    def it_folds_the_hours_and_closes_the_equipment_with_the_active_switch(live_server, page, login_via_code):
        MembershipPlanFactory()  # so the user signal provisions the member
        user = User.objects.create_user(username=MANAGER_EMAIL, email=MANAGER_EMAIL)
        member = user.member
        member.status = Member.Status.ACTIVE
        member.fog_role = Member.FogRole.ADMIN
        member.save(update_fields=["status", "fog_role"])
        member.sync_user_permissions()
        config = SiteConfiguration.load()
        config.late_cancel_fees_enabled = True
        config.save()
        cnc = EquipmentFactory(name="CNC Machine")
        EquipmentHoursFactory(equipment=cnc)  # Tuesday, 9 to 5
        manage_url = f"{live_server.url}{reverse('hub_equipment_manage', args=[cnc.slug])}"

        login_via_code(MANAGER_EMAIL)
        page.set_viewport_size({"width": 1100, "height": 900})
        page.goto(f"{manage_url}?tab=hours")
        row = page.locator("[data-hours-row]").first
        summary = row.locator("[data-hours-summary]")
        summary.get_by_text("Tue · 9:00 AM to 5:00 PM").wait_for()
        assert not row.locator("[data-hours-body]").is_visible()
        _capture(page, "hours-collapsed")

        row.locator("[data-hours-edit]").click()
        row.locator("[data-hours-body]").wait_for(state="visible")
        row.get_by_text("Thursday").click()
        summary.get_by_text("Tue, Thu · 9:00 AM to 5:00 PM").wait_for()
        closed_message = page.locator("[data-closed-message]")
        assert not closed_message.is_visible()
        _capture(page, "hours-limits", hover=page.locator(".pl-form-label-row .pl-help").nth(2))

        page.locator("[data-availability-card] .pl-toggle").click()
        closed_message.wait_for(state="visible")
        closed_message.locator("input").fill("Down for a new spindle.")
        # The save redirects back to this same ?tab=hours URL, so wait on the navigation itself.
        with page.expect_navigation():
            page.get_by_role("button", name="Save", exact=True).click()

        cnc.refresh_from_db()
        assert cnc.is_closed is True
        assert cnc.closed_message == "Down for a new spindle."
        assert sorted(cnc.hours_rules.values_list("weekday", flat=True)) == [1, 3]
        page.locator("[data-closed-message]").wait_for(state="visible")
        _capture(page, "hours-closed")

        if CAPTURE_DIR:
            page.goto(f"{manage_url}?tab=reservations")
            page.locator("[data-late-fee-intro]").wait_for()
            _capture(page, "reservations")
