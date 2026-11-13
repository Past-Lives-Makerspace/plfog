"""Browser flow for Get Set Up part 3 (#1167): the grants that are not capabilities, and the cards on
Notification Settings.

A new Class Reviewer opens their guild's page and sees the card; Got It opens Review Your First
Class, a guild side review they decide finishes setup with the badge toast, and the badge is on the
Member Directory card.

With ``CAPTURE_1167C_DIR`` set, members holding the part 3 permissions are walked past every page
that hosts one of their cards (desktop light, viewport only), two of them on a phone dark too, plus
the Guide page and the Directory. ``GUIDANCE_1167C_DIR`` takes the What's New shots.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

from django.apps import apps
from django.contrib.auth.models import User
from django.urls import reverse

from classes.factories import ClassOfferingFactory
from classes.models import ClassApproval
from membership import grant_setup as gs
from membership.models import AdminCapability, Member, PermissionSetup, SetupPermission, WikiPage
from tests.e2e.guidance import shoot
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MembershipPlanFactory

Cap = AdminCapability.Capability
CAPTURE_DIR = os.environ.get("CAPTURE_1167C_DIR")
GUIDANCE_DIR = os.environ.get("GUIDANCE_1167C_DIR")
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
    if WikiPage.guide_page_for(SetupPermission.CLASS_REVIEWER) is None:
        # A transactional live server spec may start after another flushed the seeded pages.
        importlib.import_module("membership.migrations.0282_seed_grant_guides").seed(apps, None)


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
    page.evaluate(
        "() => document.querySelectorAll('.pl-toast, [data-toast], [data-pl-tour-offer]').forEach((el) => el.remove())"
    )
    if scroll_to:
        page.locator(scroll_to).first.scroll_into_view_if_needed()
    else:
        page.evaluate("() => window.scrollTo(0, 0)")
    page.wait_for_timeout(500)
    frame = "phone-dark" if phone else "desktop-light"
    page.screenshot(path=str(Path(CAPTURE_DIR) / f"1167c-{name}-{frame}.png"))
    page.set_viewport_size(DESKTOP)
    page.evaluate("() => document.documentElement.removeAttribute('data-theme')")


def _guidance_shot(page, step: int, target: str | None = None) -> None:
    if GUIDANCE_DIR:
        page.evaluate("() => document.querySelectorAll('[data-pl-tour-offer]').forEach((el) => el.remove())")
        shoot(page, Path(GUIDANCE_DIR) / f"step-{step}.png", target=target)
        page.evaluate("() => document.documentElement.removeAttribute('data-theme')")


def describe_grant_get_set_up():
    def it_walks_a_new_class_reviewer_from_the_first_visit_to_the_badge(live_server, page, login_via_code):
        _seed_guides()
        lead = _person("lead-1167c@example.com", "Marisol Quenby")
        reviewer = _person("reviewer-1167c@example.com", "Teodor Ashgrove")
        guild = GuildFactory(name="Quillmark Bindery Guild", guild_lead=lead)
        GuildStaffMembershipFactory(guild=guild, member=reviewer, can_review_classes=True)
        login_via_code("reviewer-1167c@example.com")
        guild_page = f"{live_server.url}{reverse('hub_guild_edit', args=[guild.pk])}"

        page.goto(guild_page)
        card = page.locator('[data-permission-setup="class_reviewer"]')
        card.wait_for()
        assert "Questions? Ask Marisol Quenby." in card.inner_text()
        _guidance_shot(page, 1, target="permissions.get-set-up")

        card.get_by_role("button", name="Got It").click()
        card.locator('[data-setup-open][data-setup-step="first_review"]').wait_for()
        _guidance_shot(page, 2, target="permissions.get-set-up")

        ClassApproval.objects.create(
            class_offering=ClassOfferingFactory(),
            role=ClassApproval.Role.GUILD_LEAD,
            decided_by=reviewer.user,
            decision=ClassApproval.Decision.APPROVED,
        )
        page.goto(guild_page)
        # The server renders the message hidden and the toast script shows it; wait on the shown one.
        page.locator(f"text={gs.CLASS_REVIEWER_SETUP.celebration} >> visible=true").first.wait_for()
        page.locator('[data-permission-setup="class_reviewer"][data-setup-done]').wait_for()
        _guidance_shot(page, 3, target="permissions.get-set-up")

        page.goto(f"{live_server.url}{reverse('hub_member_directory')}")
        page.locator(".directory-card .pl-leader-badge", has_text="Class Reviewer").first.wait_for()

    def it_shows_each_card_on_its_page(live_server, page, login_via_code):
        if not CAPTURE_DIR:
            return
        _seed_guides()
        admin = _person("admin-1167c@example.com", "Ines Calloway", fog_role=Member.FogRole.ADMIN)
        for capability in (Cap.DISCOUNT_APPROVER, Cap.REFUNDS, Cap.WEBMASTER):
            admin.admin_capabilities.create(capability=capability)
        admin.grant_instructor(granted_by=None)
        login_via_code("admin-1167c@example.com")

        page.goto(f"{live_server.url}{reverse('hub_user_settings')}?tab=notifications")
        page.locator('[data-permission-setup="discount_approver"]').wait_for()
        _capture(page, "first-notifications", scroll_to='[data-permission-setup="discount_approver"]')
        _capture(page, "first-notifications", phone=True, scroll_to='[data-permission-setup="discount_approver"]')

        page.goto(f"{live_server.url}{reverse('hub_admin_tools')}")
        page.locator('[data-permission-setup="admin_role"]').wait_for()
        _capture(page, "first-admin", scroll_to='[data-permission-setup="admin_role"]')

        page.goto(f"{live_server.url}{reverse('classes:teach_overview')}")
        page.locator('[data-permission-setup="instructor"]').wait_for()
        _capture(page, "first-instructor", scroll_to='[data-permission-setup="instructor"]')
        _capture(page, "first-instructor", phone=True, scroll_to='[data-permission-setup="instructor"]')

        guild = GuildFactory(name="Tarnwell Glass Guild")
        GuildStaffMembershipFactory(guild=guild, member=admin, can_review_classes=True)
        page.goto(f"{live_server.url}{reverse('hub_guild_edit', args=[guild.pk])}")
        page.locator('[data-permission-setup="class_reviewer"]').wait_for()
        _capture(page, "first-class_reviewer", scroll_to='[data-permission-setup="class_reviewer"]')

        page.goto(f"{live_server.url}{reverse('hub_permission_guide', args=[SetupPermission.INSTRUCTOR])}")
        page.locator("[data-guide-page]").wait_for()
        _capture(page, "guide")

        PermissionSetup.for_member(admin, Cap.WEBMASTER).mark_read("learn")
        PermissionSetup.for_member(admin, SetupPermission.ADMIN_ROLE).mark_read("learn")
        page.goto(f"{live_server.url}{reverse('hub_user_settings')}?tab=notifications")
        page.goto(f"{live_server.url}{reverse('hub_admin_tools')}")
        page.locator('[data-permission-setup="admin_role"][data-setup-done]').wait_for()
        page.goto(f"{live_server.url}{reverse('hub_member_directory')}")
        page.locator(".directory-card .pl-leader-badge", has_text="Webmaster").first.wait_for()
        _capture(page, "directory", scroll_to=".directory-card:has(.pl-leader-badge)")
