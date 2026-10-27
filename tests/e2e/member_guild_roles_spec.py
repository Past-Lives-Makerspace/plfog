"""End-to-end: an admin makes Erin a Class Reviewer from her member page, and the guild sees it (#968).

Erin is an Orienter on the Printmaking Guild with Class Reviewer off. On her Edit page's
Permissions tab the admin flips that guild's Class Reviewer switch (saved over htmx, no reload),
then uses Add to a Guild with only Class Reviewer on to make her a reviewer of the Wood Guild,
which she was not on. Both guilds' Staff tabs then show her switch on. Waits are on what the page
shows, never a fixed sleep. ``CAPTURE_968_SCREENSHOT=1`` also saves the PR's pictures (both
themes at 1280) under ``mockups/screenshots/``. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import GuildStaffMembership, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MemberFactory, MembershipPlanFactory

ADMIN_EMAIL = "member-guild-roles-admin@example.com"
CAPTURE = bool(os.environ.get("CAPTURE_968_SCREENSHOT"))
SHOTS = Path("mockups/screenshots")


def _seed():
    MembershipPlanFactory()
    user = User.objects.create_user(username=ADMIN_EMAIL, email=ADMIN_EMAIL)
    admin = user.member
    admin.fog_role = Member.FogRole.ADMIN
    admin.status = Member.Status.ACTIVE
    admin.save(update_fields=["fog_role", "status"])
    admin.sync_user_permissions()
    erin = MemberFactory(full_legal_name="Erin Holloway")
    printmaking = GuildFactory(name="Printmaking Guild")
    GuildStaffMembershipFactory(guild=printmaking, member=erin, role=GuildStaffMembership.Role.ORIENTER)
    ceramics = GuildFactory(name="Ceramics Guild", guild_lead=erin)
    wood = GuildFactory(name="Wood Guild", guild_lead=MemberFactory(full_legal_name="Sam Ortega"))
    return erin, printmaking, ceramics, wood


def _staff_tab_switch(page, live_server, guild, erin):
    page.goto(f"{live_server.url}{reverse('hub_guild_edit', args=[guild.pk])}?tab=staff")
    return page.get_by_label(f"Class Reviewer: {erin.display_name}")


def _capture(page, selector: str, name: str) -> None:
    """Save ``selector`` in both themes, clipped from a full page shot so fixed toasts and bars stay off it."""
    SHOTS.mkdir(parents=True, exist_ok=True)
    decline = page.locator("[data-tour-decline]")
    if decline.is_visible():
        decline.click()
    page.evaluate("document.querySelectorAll('.pl-toast, [x-data*=toast]').forEach(e => e.remove())")
    page.add_style_tag(content="*, *::before, *::after { transition: none !important; }")  # no mid fade theme shots
    for theme in ("dark", "light"):
        page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
        box = page.locator(selector).evaluate(
            "e => { const r = e.getBoundingClientRect();"
            " return {x: r.x + scrollX - 16, y: r.y + scrollY - 16, width: r.width + 32, height: r.height + 32}; }"
        )
        page.screenshot(path=str(SHOTS / f"{name}-{theme}.png"), full_page=True, clip=box)


def describe_member_guild_roles():
    def it_flips_class_reviewer_on_from_the_member_page_and_the_staff_tab_agrees(live_server, page, login_via_code):
        erin, printmaking, ceramics, wood = _seed()
        login_via_code(ADMIN_EMAIL)
        page.set_viewport_size({"width": 1280, "height": 900})
        edit_url = f"{live_server.url}{reverse('hub_admin_member_edit', args=[erin.pk])}?tab=permissions"
        page.goto(edit_url)

        switch = page.get_by_label("Class Reviewer: Printmaking Guild")
        expect(switch).not_to_be_checked()
        expect(page.locator(f'[data-guild-role="{ceramics.pk}"]')).to_contain_text("always, as guild lead")
        with page.expect_response(lambda r: "class-reviewer" in r.url and r.request.method == "POST") as posted:
            page.locator(f'[data-class-reviewer-guild="{printmaking.pk}"] .pl-toggle__slider').click()
        assert posted.value.status == 204
        expect(page.get_by_text("Erin Holloway now reviews Printmaking Guild classes.")).to_be_visible()
        expect(switch).to_be_checked()

        # Add to a Guild with only Class Reviewer on: she joins the Wood Guild as its Class Reviewer.
        form = page.locator("[data-guild-staff-add]")
        form.get_by_label("Guild").select_option(label="Wood Guild")
        form.locator("label.pl-toggle").click()
        form.get_by_role("button", name="Add to Guild").click()
        expect(page.get_by_label("Class Reviewer: Wood Guild")).to_be_checked()
        expect(page.locator(f'[data-guild-role="{wood.pk}"]')).to_contain_text("Class Reviewer")

        if CAPTURE:
            _capture(page, "#guild-roles", "968-guild-roles")

        expect(_staff_tab_switch(page, live_server, printmaking, erin)).to_be_checked()
        expect(_staff_tab_switch(page, live_server, wood, erin)).to_be_checked()
        assert GuildStaffMembership.objects.get(guild=wood, member=erin).custom_title == "Class Reviewer"

        if CAPTURE:
            _capture(page, 'div.hub-card[data-help-key="guild.manage-staff"]', "968-staff-tab")
