"""End-to-end: a member with neither press orientation sees each one with its own Book link (#656).

The etching press is one item unlocked by either Beginner or Experienced. A member who holds
neither opens its page and the requirements banner says any one of them unlocks it, with a Book
link per orientation that lands on that orientation. ``CAPTURE_656_SCREENSHOT=1`` also saves the
PR's picture under ``mockups/screenshots/``. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse

from membership.models import Member
from tests.membership.factories import (
    EquipmentFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationTypeFactory,
)

MEMBER_EMAIL = "equipment-any-orientation@example.com"
CAPTURE = bool(os.environ.get("CAPTURE_656_SCREENSHOT"))
SHOTS = Path("mockups/screenshots")


def describe_equipment_any_orientation():
    def it_lists_each_unlocking_orientation_with_its_own_book_link(live_server, page, login_via_code):
        MembershipPlanFactory()  # so the user signal provisions the member
        user = User.objects.create_user(username=MEMBER_EMAIL, email=MEMBER_EMAIL)
        user.member.status = Member.Status.ACTIVE
        user.member.save(update_fields=["status"])
        guild = GuildFactory(name="Printmaking")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
        beginner = OrientationTypeFactory(guild=guild, name="Etching Press Beginner")
        experienced = OrientationTypeFactory(guild=guild, name="Etching Press Experienced")
        press = EquipmentFactory(name="Etching Press", guild=guild, unlocking_orientations=[beginner, experienced])

        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_equipment_detail', args=[press.slug])}")
        banner = page.locator(".pl-equip-banner--warn")
        banner.get_by_text("Any one of these orientations unlocks this.").wait_for()
        for orientation_type in (beginner, experienced):
            row = banner.locator(f"[data-unlock-type='{orientation_type.pk}']")
            assert orientation_type.name in row.inner_text()
            book = row.get_by_role("link", name="Book")
            assert book.get_attribute("href") == orientation_type.orientation_anchor_path()
        if CAPTURE:
            SHOTS.mkdir(parents=True, exist_ok=True)
            page.set_viewport_size({"width": 1100, "height": 900})
            banner.screenshot(path=str(SHOTS / "656-equipment-any-orientation.png"))
