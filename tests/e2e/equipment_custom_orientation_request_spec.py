"""End-to-end: equipment takes orientation requests at a time the member proposes (#733).

A manager of the CNC Machine turns on "Let members propose their own orientation time" on the
Orientation tab and saves. A member, with no orientation times posted, sees the propose a time
form on the equipment page and on the Orientations page card, sends a request from the card, and
the request lands in the manager's Pending Requests. ``CAPTURE_733_DIR=<dir>`` also saves dark
and light pictures of the switch and of both forms there. Run with ``pytest -m e2e`` on
PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from membership.models import Member, OrientationBooking
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
)

MANAGER_EMAIL = "equipment-custom-manager@example.com"
MEMBER_EMAIL = "equipment-custom-member@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_733_DIR")


def _member(email: str, name: str) -> Member:
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.preferred_name = name
    member.full_legal_name = f"{name} Tester"
    member.save(update_fields=["status", "preferred_name", "full_legal_name"])
    return member


def _capture(page, name: str, target=None) -> None:
    """Save a dark and a light picture when CAPTURE_733_DIR is set: the whole page, or ``target``."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for theme in ("dark", "light"):
        page.evaluate(
            "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light') : document.documentElement.removeAttribute('data-theme')",
            theme,
        )
        page.wait_for_timeout(600)
        path = str(Path(CAPTURE_DIR) / f"733-{name}-{theme}.png")
        if target is None:
            page.screenshot(path=path)
        else:
            target.screenshot(path=path)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")


def describe_equipment_custom_orientation_requests():
    def it_lets_a_member_propose_a_time_that_reaches_the_manager(live_server, page, login_via_code):
        MembershipPlanFactory()  # so the user signal provisions the member
        manager = _member(MANAGER_EMAIL, "Dana")
        requester = _member(MEMBER_EMAIL, "Robin")
        cnc = EquipmentFactory(name="CNC Machine", allow_custom_requests=False)
        EquipmentStaffMembershipFactory(equipment=cnc, member=manager)
        orientation_type = OrientationTypeFactory(
            equipment_owned=True, equipment=cnc, name="CNC Operator Basics", duration_minutes=90
        )
        manage_url = f"{live_server.url}{reverse('hub_equipment_manage', args=[cnc.slug])}?tab=orientation"
        page.set_viewport_size({"width": 1100, "height": 900})

        # The manager turns the switch on and saves it with the Orientations card.
        login_via_code(MANAGER_EMAIL)
        page.goto(manage_url)
        switch_row = page.locator("[data-orientation-requests]")
        switch_row.wait_for(state="visible")
        assert not switch_row.locator("input[type=checkbox]").is_checked()
        switch_row.locator(".pl-toggle").click()
        assert switch_row.locator("input[type=checkbox]").is_checked()
        _capture(page, "switch", target=page.locator("form:has([data-orientation-requests]) > .hub-card"))
        with page.expect_navigation():
            page.locator("form:has([data-orientation-requests]) button[type=submit]").click()
        cnc.refresh_from_db()
        assert cnc.allow_custom_requests is True
        page.locator("[data-orientation-requests] input[type=checkbox]:checked").wait_for(state="attached")

        # The member sees the form on the equipment page, with no times posted.
        page.context.clear_cookies()
        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_equipment_detail', args=[cnc.slug])}")
        section = page.locator(f"#orientation-type-{orientation_type.pk}")
        section.get_by_role("button", name="Schedule an Orientation").click()
        section.locator(f"#id_custom_{orientation_type.pk}_starts_at").wait_for(state="visible")
        _capture(page, "equipment-form", target=page.locator("#equipment-orientation"))

        # And on the Orientations page card, where they send it.
        page.goto(f"{live_server.url}{reverse('hub_orientations')}")
        card = page.locator(f"#orientation-type-{orientation_type.pk}")
        card.get_by_role("button", name="Schedule an Orientation").click()
        starts = card.locator(f"#id_custom_{orientation_type.pk}_starts_at")
        starts.wait_for(state="visible")
        when = timezone.localtime(timezone.now() + timedelta(days=5)).replace(hour=14, minute=0)
        starts.fill(when.strftime("%Y-%m-%dT%H:%M"))
        card.locator(f"#id_custom_{orientation_type.pk}_note").fill("Weekday afternoons work for me.")
        _capture(page, "card-form", target=card)
        with page.expect_navigation():
            card.get_by_role("button", name="Send request").click()

        booking = OrientationBooking.objects.get(member=requester)
        assert booking.status == OrientationBooking.Status.REQUESTED
        assert booking.orientation_type == orientation_type
        assert booking.slot.seats == 1
        assert timezone.localtime(booking.slot.starts_at) == when.replace(second=0, microsecond=0)
        assert booking.slot.ends_at - booking.slot.starts_at == timedelta(minutes=90)

        # The request waits for the manager on the Orientation tab, one Review away.
        page.context.clear_cookies()
        login_via_code(MANAGER_EMAIL)
        page.goto(manage_url)
        respond = reverse("hub_orientation_respond", args=[booking.pk])
        page.get_by_role("link", name="Review").and_(page.locator(f"a[href='{respond}']")).wait_for(state="visible")
        assert page.locator(".pl-equip-res-row", has_text=requester.display_name).is_visible()
