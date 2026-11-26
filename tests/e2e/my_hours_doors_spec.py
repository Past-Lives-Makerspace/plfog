"""End-to-end: every place hours were set opens My Hours (#1300, part 5), in a real browser.

Robin gives tours, leads the Printmaking Guild and runs the Etching Press. Each door lands on My
Hours: the Tours page's My Hours tab, Add My Hours on a Calendar gap (+ Add Dates open on that day),
Edit Hours and Days Off on the guild's and the tool's Orientation Schedule, and Edit Hours under
Your Hours on Manage Bookings. An admin's Edit Hours on Robin's row opens Robin's My Hours with the
picker on Robin. A paused orientation shows unticked under Open To and ticks back on.

``CAPTURE_1300P5_DIR=<dir>`` saves each door and My Hours at 1280x800 and 390 wide, dark and light,
for the PR; ``GUIDANCE_1300P5_DIR=<dir>`` saves the What's New steps to the standard frame. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from datetime import time, timedelta
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from membership.models import AdminCapability, Member, OrientationAvailability
from tests.e2e.guidance import shoot
from tests.membership.factories import (
    EquipmentFactory,
    EquipmentStaffMembershipFactory,
    GuildFactory,
    GuildOrientationSettingsFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
    OrientationTypeFactory,
)
from tests.tours.factories import TourDayOffFactory, TourHoursFactory

EMAIL = "robin.v@example.com"
ADMIN_EMAIL = "ada.a@example.com"
CAPTURE_DIR = os.environ.get("CAPTURE_1300P5_DIR")
GUIDANCE_DIR = os.environ.get("GUIDANCE_1300P5_DIR")
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}
MY_HOURS = reverse("hours:my_hours")


def _capture(page, name: str, anchor: str | None = None) -> None:
    """Save the viewport, dark and light, at desktop and phone widths, ``anchor`` scrolled into view."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    for size_name, size in (("desktop", DESKTOP), ("phone", PHONE)):
        page.set_viewport_size(size)
        page.evaluate("(v) => { Alpine.$data(document.body).sidebarOpen = v; }", size is DESKTOP)
        for theme in ("dark", "light"):
            page.evaluate(
                "(t) => t === 'light' ? document.documentElement.setAttribute('data-theme', 'light')"
                " : document.documentElement.removeAttribute('data-theme')",
                theme,
            )
            page.wait_for_timeout(500)
            if anchor:
                page.locator(anchor).first.evaluate("(el) => el.scrollIntoView({ block: 'center' })")
                page.wait_for_timeout(250)
            page.screenshot(path=str(Path(CAPTURE_DIR) / f"1300p5-{name}-{size_name}-{theme}.png"))
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")
    page.set_viewport_size(DESKTOP)


def _guidance(page, step: int, target: str, anchor: str | None = None) -> None:
    """A What's New step to the standard frame, ``anchor`` (else the first visible target) scrolled into view."""
    if GUIDANCE_DIR:
        page.set_viewport_size(DESKTOP)
        page.locator(anchor or f'[data-guidance-target="{target}"]:visible').first.evaluate(
            "(el) => el.scrollIntoView({ block: 'center' })"
        )
        shoot(page, Path(GUIDANCE_DIR) / f"step-{step}.png", target=target)
        page.evaluate("() => document.querySelectorAll('style[data-guidance-ring]').forEach((s) => s.remove())")


def _person(email: str, name: str, **fields: object) -> Member:
    MembershipPlanFactory()  # so the user signal provisions the member
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    for field, value in fields.items():
        setattr(member, field, value)
    member.save()
    return member


def _robin() -> tuple[Member, object, object]:
    robin = _person(EMAIL, "Robin Vale")
    robin.admin_capabilities.create(capability=AdminCapability.Capability.TOUR_GUIDE)
    guild = GuildFactory(name="Printmaking Guild", guild_lead=robin)
    GuildOrientationSettingsFactory(guild=guild)
    intro = OrientationTypeFactory(guild=guild, name="Complete Beginner to Printmaking")
    press = EquipmentFactory(name="Etching Press", guild=guild)
    EquipmentStaffMembershipFactory(equipment=press, member=robin)
    OrientationTypeFactory(equipment_owned=True, equipment=press, name="Etching Press Checkout")
    TourHoursFactory(guide=robin, weekday=0, start_time=time(11), end_time=time(18), seats=6)
    OrientationAvailabilityFactory(
        guild=guild, orientation_type=intro, orienter=robin, weekday=0, start_time=time(11), end_time=time(18)
    )
    # Wednesday's print orientation is paused: it shows unticked, its settings kept.
    OrientationAvailabilityFactory(
        guild=guild,
        orientation_type=intro,
        orienter=robin,
        weekday=2,
        start_time=time(12),
        end_time=time(17),
        seats=3,
        is_active=False,
    )
    today = timezone.localdate()
    TourDayOffFactory(guide=robin, starts_on=today + timedelta(days=10), ends_on=today + timedelta(days=12))
    return robin, guild, press


def _lands_on_my_hours(page, live_server, query: str = "") -> None:
    expect(page).to_have_url(f"{live_server.url}{MY_HOURS}{query}")
    page.locator("[data-hours-week]").wait_for()


def describe_every_door():
    def it_opens_my_hours_from_each_place_hours_were_set(live_server, page, login_via_code):
        robin, guild, press = _robin()
        login_via_code(EMAIL)
        page.set_viewport_size(DESKTOP)

        # The Tours page: the My Hours tab opens My Hours.
        page.goto(f"{live_server.url}{reverse('tours:upcoming')}")
        tab = page.locator('nav.pl-tabs [data-tours-tab="hours"]')
        tab.wait_for()
        _capture(page, "tours-tab")
        _guidance(page, 1, "tours.my-hours")
        with page.expect_navigation():
            tab.click()
        _lands_on_my_hours(page, live_server)
        # A Tour Guide's Get Set Up card came along from the Tours tab.
        page.locator('[data-permission-setup="tour_guide"]').first.wait_for()

        # A Calendar gap: Add My Hours opens + Add Dates on that day.
        page.goto(f"{live_server.url}{reverse('tours:calendar')}")
        gap = page.locator("[data-tours-gap]").first
        gap.wait_for()
        day = gap.get_attribute("data-tours-gap")
        with page.expect_navigation():
            gap.locator("[data-tours-gap-add]").click()
        page.locator("[data-hours-dates-panel]").wait_for(state="visible")
        page.locator(f'[data-hours-date="{day}"][aria-pressed="true"]').wait_for()

        # The guild's Orientation Schedule: Edit Hours and Days Off.
        orientations = reverse("hub_guild_orientations", args=[guild.pk])
        page.goto(f"{live_server.url}{orientations}")
        edit = page.locator("[data-edit-hours-link]")
        expect(edit).to_have_count(1)
        _capture(page, "guild-schedule", "[data-edit-hours-link]")
        with page.expect_navigation():
            edit.click()
        _lands_on_my_hours(page, live_server)
        page.goto(f"{live_server.url}{orientations}")
        with page.expect_navigation():
            page.locator("[data-days-off-button]").click()
        expect(page).to_have_url(f"{live_server.url}{MY_HOURS}#days-off")
        page.locator("[data-hours-days-off]").wait_for()

        # The tool's Orientation Schedule.
        page.goto(f"{live_server.url}{reverse('hub_equipment_manage', args=[press.slug])}?tab=orientation")
        tool_edit = page.locator("[data-edit-hours-link]")
        tool_edit.wait_for()
        _capture(page, "tool-schedule", "[data-edit-hours-link]")
        with page.expect_navigation():
            tool_edit.click()
        _lands_on_my_hours(page, live_server)

        # Manage Bookings: Edit Hours beside each guild and tool under Your Hours.
        page.goto(f"{live_server.url}{reverse('hub_manage_bookings')}")
        your_hours = page.locator("[data-your-hours]")
        your_hours.wait_for()
        expect(your_hours.locator("[data-edit-hours]")).to_have_count(1)
        _capture(page, "manage-bookings", "[data-your-hours]")
        with page.expect_navigation():
            your_hours.locator("[data-edit-hours]").first.click()
        _lands_on_my_hours(page, live_server)
        _capture(page, "my-hours")

        # Open To on Monday's hours: Tours and the print orientation ticked, the press offered.
        rows = page.locator("[data-hours-weekly] [data-hours-set]")
        expect(rows).to_have_count(2)
        monday = rows.nth(0)
        monday.locator("[data-hours-set-edit]").click()
        monday.locator("[data-hours-open-to]").wait_for()
        _guidance(page, 2, "hours.open-to", anchor="[data-hours-weekly] [data-hours-set] [data-hours-open-to]:visible")
        monday.locator("[data-hours-set-edit]").click()

        # The paused print orientation shows unticked, and ticks back on with its seats.
        rule = OrientationAvailability.objects.get(weekday=2)
        wednesday = rows.nth(1)
        wednesday.locator("[data-hours-set-edit]").click()
        paused = wednesday.locator(f'[data-hours-kind="rule-{rule.pk}"]')
        paused.wait_for()
        expect(paused).to_contain_text("Paused. Tick it to offer it again with these settings.")
        _capture(page, "my-hours-paused", f'[data-hours-kind="rule-{rule.pk}"]')
        paused.locator(".pl-toggle").click()
        page.wait_for_function(
            "() => { const pill = document.querySelector('[data-tour-save-status=\"weekly\"]');"
            " const root = document.querySelector('[data-guild-autosave]').plGuildAutosave;"
            " return !!pill && root.pending === 0 && Number(pill.dataset.saves) >= 1"
            " && pill.textContent.trim() === 'Saved'; }"
        )
        rule.refresh_from_db()
        assert rule.is_active and rule.seats == 3

        # Days Off: one list for everything.
        page.locator("[data-hours-days-off]").evaluate("(el) => el.scrollIntoView({ block: 'center' })")
        _guidance(page, 3, "hours.add-day-off")

    def it_opens_a_staffers_my_hours_for_an_admin(live_server, page, login_via_code):
        robin, guild, _press = _robin()
        _person(ADMIN_EMAIL, "Ada Admin", fog_role=Member.FogRole.ADMIN)
        MemberFactory()
        login_via_code(ADMIN_EMAIL)
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('hub_guild_orientations', args=[guild.pk])}")
        edit = page.locator(f'[data-edit-hours-link][href="{MY_HOURS}?member={robin.pk}"]')
        edit.wait_for()
        with page.expect_navigation():
            edit.click()
        _lands_on_my_hours(page, live_server, f"?member={robin.pk}")
        expect(page.locator("#hours-member-picker")).to_have_value(str(robin.pk))
        _capture(page, "admin-picker", "[data-hours-picker]")
