"""End-to-end: waiting orientation requests on Home (#1299), Needs Attention and the Guild Activity card.

The viewer is an Orientor of one guild (a request waits on them), the lead of another and the
Treasurer of the guild that owns an item (those requests wait on someone else). Needs Attention
shows the first with Respond; Guild Activity shows the others, each with a ? tip naming who was
notified. Run with ``pytest -m e2e`` on PostgreSQL. Set ``ORIENTATION_SHOTS=<folder>`` to keep
the guidance shots (``docs/GUIDANCE.md``) and the PR's desktop and phone shots in both themes
there; otherwise they go to a temporary folder, so the spec still proves every shot can be taken.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership import orientations
from membership.models import Guild, GuildStaffMembership, Member, OrientationBooking
from tests.e2e.guidance import FRAMES, shoot
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildStaffMembershipFactory,
    MembershipPlanFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

VIEWER_EMAIL = "orientation-requests-viewer@example.com"


def _person(email: str, name: str) -> Member:
    user = User.objects.filter(username=email).first() or User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.save(update_fields=["status", "full_legal_name"])
    return member


def _guild(name: str, lead: Member) -> Guild:
    return GuildFactory(name=name, guild_lead=lead)


def _ask(slot_kwargs: dict, asker: Member, *, days_ago: int) -> OrientationBooking:
    booking = orientations.request_orientation(OrientationSlotFactory(**slot_kwargs), asker)
    OrientationBooking.objects.filter(pk=booking.pk).update(requested_at=timezone.now() - timedelta(days=days_ago))
    return booking


def _when(days: int, hour: int) -> dict:
    start = timezone.localtime() + timedelta(days=days)
    start = start.replace(hour=hour, minute=0, second=0, microsecond=0)
    return {"starts_at": start, "ends_at": start + timedelta(hours=1)}


def describe_orientation_requests_on_home():
    def it_shows_the_request_on_the_viewer_and_the_ones_on_others(live_server, page, login_via_code, tmp_path):
        shots = Path(os.environ.get("ORIENTATION_SHOTS") or tmp_path)
        MembershipPlanFactory()  # so the user signal provisions each member
        robin = _person(VIEWER_EMAIL, "Robin Vale")

        ceramics = _guild("Ceramics Guild", _person("kai@example.com", "Kai Lund"))
        GuildStaffMembershipFactory(guild=ceramics, member=robin, role=GuildStaffMembership.Role.ORIENTER)
        wheel = OrientationTypeFactory(guild=ceramics, name="Ceramics Guild Orientation")
        mine = _ask(
            {"guild": ceramics, "orientation_type": wheel, **_when(2, 10)},
            _person("avery@example.com", "Avery Chen"),
            days_ago=1,
        )

        metal = _guild("Metalworkers Guild", robin)
        for email, name in (("rae@example.com", "Rae Kim"), ("theo@example.com", "Theo Marsh")):
            GuildStaffMembershipFactory(
                guild=metal, member=_person(email, name), role=GuildStaffMembership.Role.ORIENTER
            )
        weld = OrientationTypeFactory(guild=metal, name="Metalworkers Guild Orientation")
        theirs = _ask(
            {"guild": metal, "orientation_type": weld, **_when(3, 13)},
            _person("jordan@example.com", "Jordan Ellis"),
            days_ago=3,
        )

        fab = _guild("Digital Fabrication", _person("pat@example.com", "Pat Rowe"))
        GuildStaffMembershipFactory(guild=fab, member=robin, role=GuildStaffMembership.Role.TREASURER)
        cnc = EquipmentFactory(name="CNC Machine", guild=fab)
        dee = _person("dee@example.com", "Dee Ortiz")
        EquipmentStaffMembershipFactory(equipment=cnc, member=dee)
        router = OrientationTypeFactory(guild=None, equipment=cnc, name="CNC Machine Orientation")
        item = _ask(
            {"guild": None, "orientation_type": router, "orienter": dee, **_when(5, 18)},
            _person("sam@example.com", "Sam Okafor"),
            days_ago=0,
        )
        home = f"{live_server.url}{reverse('hub_home')}"

        login_via_code(VIEWER_EMAIL)
        page.set_viewport_size(FRAMES["desktop"])
        page.goto(home)
        page.wait_for_load_state("networkidle")

        group = page.locator("[data-needs-attention-orientations]")
        expect(group.locator("[data-orientation-request]")).to_have_count(1)
        expect(group).to_contain_text("Avery Chen · Ceramics Guild Orientation")
        expect(group).to_contain_text("Requested yesterday")
        expect(group.get_by_role("link", name="Respond")).to_have_attribute(
            "href", reverse("hub_orientation_respond", args=[mine.pk])
        )
        card = page.locator('[data-block="guild-activity"]')
        expect(card.locator("[data-guild-activity]")).to_have_count(2)
        metal_row = card.locator(f'[data-guild-activity="{theirs.pk}"]')
        expect(metal_row).to_contain_text("Jordan Ellis · Metalworkers Guild Orientation")
        expect(metal_row).to_contain_text("requested 3 days ago")
        expect(metal_row.get_by_role("link", name="View")).to_have_attribute(
            "href", reverse("hub_orientation_respond", args=[theirs.pk])
        )
        expect(card.locator(f'[data-guild-activity="{item.pk}"]')).to_contain_text("requested today")

        tip = metal_row.locator(".pl-help__icon")
        bubble = metal_row.locator("[data-guild-activity-tip]")
        expect(bubble).to_be_hidden()
        tip.hover()
        tip.focus()
        expect(bubble).to_be_visible()
        expect(bubble).to_have_text(
            "Rae Kim and Theo Marsh were notified and should respond. "
            "This is here so you can see it; you don't need to act."
        )

        # The guidance shots: the desktop frame, dark, each step's control ringed; the tip shut for step 1.
        page.mouse.move(0, 0)
        page.evaluate("() => document.activeElement && document.activeElement.blur()")
        expect(bubble).to_be_hidden()
        group.scroll_into_view_if_needed()
        page.evaluate("() => window.scrollBy(0, -24)")
        shoot(page, shots / "step-1.png", target="home.orientation-requests")
        tip.hover()
        tip.focus()
        shoot(page, shots / "step-2.png", target="home.guild-activity")

        # The PR's shots: desktop and phone, dark and light, the tip open, each loaded fresh at its size.
        for name, frame, light in (
            ("desktop-dark.png", "desktop", False),
            ("desktop-light.png", "desktop", True),
            ("phone-dark.png", "phone", False),
            ("phone-light.png", "phone", True),
        ):
            page.set_viewport_size(FRAMES[frame])
            page.goto(home)
            page.wait_for_load_state("networkidle")
            page.evaluate(f"() => window.__plSetTheme && window.__plSetTheme({'true' if light else 'false'})")
            group.scroll_into_view_if_needed()
            page.evaluate("() => window.scrollBy(0, -24)")
            tip.hover()
            tip.focus()
            expect(bubble).to_be_visible()
            page.wait_for_timeout(400)  # the bubble's fade in settles
            page.screenshot(path=str(shots / name))
