"""End-to-end: Home's Edit Your Guilds (#1318), joining and leaving guilds from the Your Guilds card.

Edit opens the modal of join switches; flipping one joins the guild and the chips behind the modal
follow without a reload; flipping it off leaves. Run with ``pytest -m e2e`` on PostgreSQL. Set
``GUILDS_EDIT_SHOTS=<folder>`` to keep the guidance shots (``docs/GUIDANCE.md``) and the phone and
light theme checks there; otherwise they go to a temporary folder, so the spec still proves every
shot can be taken.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import GuildMembership, Member
from tests.e2e.guidance import FRAMES, shoot
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MembershipPlanFactory

MEMBER_EMAIL = "home-guilds-edit-member@example.com"
GUILD_ABOUTS = {
    "Ceramics Guild": "A studio for potters of every level, with wheels, glazes and kilns for throwing and handbuilding.",
    "Glass Guild": "Stained glass, torchworking and fused glass, with a cold room and a kiln.",
    "Jewelry Guild": "Benches, torches and polishing for jewelry makers, lapidary and fine metalsmithing.",
    "Metal Guild": "Forging, fabrication and welding, with tools for nearly any kind of metal work.",
    "Textiles Guild": "Sewing, quilting, weaving and fiber arts, with machines and space to spread out.",
    "Wood Guild": "A full wood shop for carpenters, furniture makers and hobbyists.",
}


def describe_edit_your_guilds_on_home():
    def it_joins_and_leaves_from_the_modal_without_a_reload(live_server, page, login_via_code, tmp_path):
        shots = Path(os.environ.get("GUILDS_EDIT_SHOTS") or tmp_path)
        MembershipPlanFactory()  # so the user signal provisions the member
        user = User.objects.create_user(username=MEMBER_EMAIL, email=MEMBER_EMAIL)
        member = user.member
        member.status = Member.Status.ACTIVE
        member.full_legal_name = "Linnea Brackenford"
        member.save(update_fields=["status", "full_legal_name"])
        guilds = {
            name: GuildFactory(name=name, about=about, meeting_schedule="") for name, about in GUILD_ABOUTS.items()
        }
        for name in ("Ceramics Guild", "Wood Guild"):
            GuildMembershipFactory(guild=guilds[name], member=member)
        home = f"{live_server.url}{reverse('hub_home')}"
        card = page.locator(".pl-home-block", has=page.locator('[data-guidance-target="home.guilds-edit"]'))
        modal = page.get_by_role("dialog", name="Edit Your Guilds")

        def open_editor() -> None:
            card.scroll_into_view_if_needed()
            card.get_by_role("button", name="Edit", exact=True).click()
            expect(modal).to_be_visible()

        login_via_code(MEMBER_EMAIL)
        page.set_viewport_size(FRAMES["desktop"])
        page.goto(home)
        card.scroll_into_view_if_needed()
        shoot(page, shots / "step-1.png", target="home.guilds-edit")
        open_editor()
        shoot(page, shots / "step-2.png")

        # The phone frame and the light theme, each loaded fresh at its own size, for the PR's checks.
        for prefix, frame, light in (
            ("phone-dark", "phone", False),
            ("phone-light", "phone", True),
            ("desktop-light", "desktop", True),
        ):
            page.set_viewport_size(FRAMES[frame])
            page.goto(home)
            if light:
                page.evaluate("() => window.__plSetTheme && window.__plSetTheme(true)")
            card.scroll_into_view_if_needed()
            page.wait_for_timeout(500)  # the sidebar and theme transitions settle
            page.screenshot(path=str(shots / f"{prefix}-card.png"))
            open_editor()
            page.wait_for_timeout(300)
            page.screenshot(path=str(shots / f"{prefix}-modal.png"))
        page.evaluate("() => window.__plSetTheme && window.__plSetTheme(false)")

        page.set_viewport_size(FRAMES["desktop"])
        page.goto(home)
        page.evaluate("() => { window.__plNoReload = true; }")
        open_editor()
        glass = modal.get_by_role("checkbox", name="Join Glass Guild")
        modal.locator(
            'label.pl-toggle:has(input[aria-label="Join Glass Guild"])'
        ).click()  # the input is drawn by its slider
        expect(glass).to_be_checked()
        expect(card.locator(".pl-guild-chip__name", has_text="Glass Guild")).to_be_visible()
        expect(page.get_by_text("You joined Glass Guild.")).to_be_visible()
        wood = modal.get_by_role("checkbox", name="Join Wood Guild")
        expect(wood).to_be_enabled()
        modal.locator('label.pl-toggle:has(input[aria-label="Join Wood Guild"])').click()
        expect(wood).not_to_be_checked()
        expect(card.locator(".pl-guild-chip__name", has_text="Wood Guild")).to_have_count(0)
        modal.get_by_role("button", name="Done").click()
        expect(modal).to_be_hidden()
        assert page.evaluate("() => window.__plNoReload") is True  # the same document: no reload
        joined = set(GuildMembership.objects.filter(member=member).values_list("guild__name", flat=True))
        assert joined == {"Ceramics Guild", "Glass Guild"}
