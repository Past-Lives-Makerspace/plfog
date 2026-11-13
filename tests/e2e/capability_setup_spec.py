"""Browser flow for the capability Get Set Up cards (#1167, part 2).

A new Space Manager opens Reservations and sees the card with Add Your First Space open; once a
space they added is on the page the next step opens, Got It finishes setup with the badge toast,
Open the Guide shows the seeded Guide page, and the badge is on the Member Directory card.

With ``CAPTURE_1167B_DIR`` set, a member holding all seven capabilities is walked past every page
that hosts a card for the PR's pictures (desktop light, viewport only), two of them on a phone
dark too, plus the Guide page and the Directory. ``GUIDANCE_1167B_DIR`` takes the What's New shots.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

from django.apps import apps
from django.contrib.auth.models import User
from django.urls import reverse

from membership import capability_setup as cs
from membership.models import AdminCapability, Equipment, EquipmentStaffMembership, Member, PermissionSetup, WikiPage
from tests.e2e.guidance import shoot
from tests.membership.factories import EquipmentFactory, MembershipPlanFactory

Cap = AdminCapability.Capability
CAPTURE_DIR = os.environ.get("CAPTURE_1167B_DIR")
GUIDANCE_DIR = os.environ.get("GUIDANCE_1167B_DIR")
DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}


def _person(email: str, name: str, *, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    user = User.objects.create_user(username=email, email=email)
    member = user.member
    member.status = Member.Status.ACTIVE
    member.full_legal_name = name
    member.fog_role = fog_role
    member.show_in_directory = True
    member.save(update_fields=["status", "full_legal_name", "fog_role", "show_in_directory"])
    member.sync_user_permissions()
    return member


def _seed_guides() -> None:
    if WikiPage.guide_page_for(Cap.SPACE_MANAGER) is None:
        # A transactional live server spec may start after another flushed the seeded pages.
        importlib.import_module("membership.migrations.0279_seed_capability_guides").seed(apps, None)


def _capture(page, name: str, *, phone: bool = False, scroll_to: str | None = None) -> None:
    """One viewport picture: desktop light with the sidebar open, or phone dark with it closed."""
    if not CAPTURE_DIR:
        return
    Path(CAPTURE_DIR).mkdir(parents=True, exist_ok=True)
    page.set_viewport_size(PHONE if phone else DESKTOP)
    page.evaluate("(v) => { if (window.Alpine) Alpine.$data(document.body).sidebarOpen = v; }", not phone)
    page.evaluate(
        "(dark) => dark ? document.documentElement.removeAttribute('data-theme')"
        " : document.documentElement.setAttribute('data-theme', 'light')",
        phone,
    )
    page.evaluate("() => document.querySelectorAll('.pl-toast, [data-toast]').forEach((el) => el.remove())")
    if scroll_to:
        page.locator(scroll_to).first.scroll_into_view_if_needed()
    else:
        page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(500)
    frame = "phone-dark" if phone else "desktop-light"
    page.screenshot(path=str(Path(CAPTURE_DIR) / f"1167b-{name}-{frame}.png"))
    page.set_viewport_size(DESKTOP)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")


def _guidance_shot(page, step: int, target: str | None = None) -> None:
    if GUIDANCE_DIR:
        shoot(page, Path(GUIDANCE_DIR) / f"step-{step}.png", target=target)
        page.evaluate("() => document.documentElement.removeAttribute('data-theme')")


def describe_capability_get_set_up():
    def it_walks_a_new_space_manager_from_the_first_visit_to_the_badge(live_server, page, login_via_code):
        _seed_guides()
        granter = _person("granter-1167b@example.com", "Marisol Quenby", fog_role=Member.FogRole.ADMIN)
        manager = _person("space-manager-1167b@example.com", "Teodor Ashgrove")
        manager.admin_capabilities.create(capability=Cap.SPACE_MANAGER, granted_by=granter.user)
        login_via_code("space-manager-1167b@example.com")
        reservations = f"{live_server.url}{reverse('hub_equipment_index')}"

        page.goto(reservations)
        card = page.locator('[data-permission-setup="space_manager"]')
        card.wait_for()
        assert card.locator("[data-setup-open]").get_attribute("data-setup-step") == cs.FIRST_SPACE
        assert "Questions? Ask Marisol Quenby." in card.inner_text()
        _guidance_shot(page, 1, target="permissions.get-set-up")

        dock = EquipmentFactory(name="North Loading Dock", kind=Equipment.Kind.SPACE)
        EquipmentStaffMembership.objects.create(equipment=dock, member=manager, granted_by=manager)
        page.goto(reservations)
        card.locator(f'[data-setup-open][data-setup-step="{cs.LEARN}"]').wait_for()
        _guidance_shot(page, 2, target="permissions.get-set-up")

        card.get_by_role("button", name="Got It").click()
        page.get_by_text(cs.SPACE_MANAGER_SETUP.celebration).first.wait_for()
        done = page.locator("[data-setup-done]")
        done.wait_for()
        assert "Space Manager" in done.inner_text()
        _guidance_shot(page, 3, target="permissions.get-set-up")

        done.get_by_role("link", name="Open the Guide").click()
        page.locator("[data-guide-page]").wait_for()
        assert "Space Manager Guide" in page.locator("h1").first.inner_text()
        page.goto(f"{live_server.url}{reverse('hub_member_directory')}")
        page.locator(".directory-card .pl-leader-badge", has_text="Space Manager").first.wait_for()

    def it_shows_each_card_on_its_page(live_server, page, login_via_code):
        if not CAPTURE_DIR:
            return
        _seed_guides()
        granter = _person("granter-1167b-all@example.com", "Marisol Quenby", fog_role=Member.FogRole.ADMIN)
        holder = _person("holder-1167b@example.com", "Ines Calloway")
        for spec in cs.CAPABILITY_SETUPS:
            holder.admin_capabilities.create(capability=spec.permission, granted_by=granter.user)
        login_via_code("holder-1167b@example.com")
        for spec in cs.CAPABILITY_SETUPS:
            page.goto(f"{live_server.url}{spec.page_url}")
            page.locator(f'[data-permission-setup="{spec.permission}"]').wait_for()
            _capture(page, f"first-{spec.permission}", scroll_to=f'[data-permission-setup="{spec.permission}"]')
            if spec.permission in (Cap.EVENT_CREATOR, Cap.EVENTS_APPROVER):
                _capture(page, f"first-{spec.permission}", phone=True, scroll_to="[data-permission-setup]")

        page.goto(f"{live_server.url}{reverse('hub_permission_guide', args=[Cap.CLASS_APPROVER])}")
        page.locator("[data-guide-page]").wait_for()
        _capture(page, "guide")

        for spec in (cs.BILLING_ADMIN_SETUP, cs.EQUIPMENT_ADMIN_SETUP):
            row = PermissionSetup.for_member(holder, spec.permission)
            for step in spec.steps:
                row.mark_read(step.key)
        page.goto(f"{live_server.url}{cs.BILLING_ADMIN_SETUP.page_url}")
        page.goto(f"{live_server.url}{cs.EQUIPMENT_ADMIN_SETUP.page_url}")
        page.locator('[data-permission-setup="equipment"][data-setup-done]').wait_for()
        dock = EquipmentFactory(name="South Freight Bay", kind=Equipment.Kind.SPACE)
        EquipmentStaffMembership.objects.create(equipment=dock, member=holder, granted_by=holder)
        PermissionSetup.for_member(holder, Cap.SPACE_MANAGER).mark_read(cs.LEARN)
        page.goto(f"{live_server.url}{cs.EQUIPMENT_ADMIN_SETUP.page_url}")
        page.locator('[data-permission-setup="space_manager"][data-setup-done]').wait_for()
        _capture(page, "done-reservations")

        page.goto(f"{live_server.url}{reverse('hub_member_directory')}")
        page.locator(".directory-card .pl-leader-badge", has_text="Space Manager").first.wait_for()
        _capture(page, "directory", scroll_to=".directory-card:has(.pl-leader-badge)")
