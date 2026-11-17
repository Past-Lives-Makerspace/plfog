"""End-to-end: Home's Reconnect Discord item (#1239), shown to a nudged member and dismissed in place.

A member linked to Discord but missing from the server has an open nudge. Home shows Reconnect
Discord under Needs Attention; Dismiss removes the card without a reload, and it stays gone on the
next visit. Run with ``pytest -m e2e`` on PostgreSQL. Set ``RECONNECT_SHOTS=<folder>`` to keep the
guidance shot (``docs/GUIDANCE.md``) and the phone and light theme checks there; otherwise they go
to a temporary folder, so the spec still proves every shot can be taken.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth.models import User
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import DiscordReconnectNudge, Member
from tests.e2e.guidance import FRAMES, shoot
from tests.membership.factories import MembershipPlanFactory

MEMBER_EMAIL = "discord-reconnect-member@example.com"


def describe_reconnect_discord_on_home():
    def it_shows_the_item_and_dismisses_it_without_a_reload(live_server, page, login_via_code, tmp_path):
        shots = Path(os.environ.get("RECONNECT_SHOTS") or tmp_path)
        MembershipPlanFactory()  # so the user signal provisions the member
        user = User.objects.create_user(username=MEMBER_EMAIL, email=MEMBER_EMAIL)
        member = user.member
        member.status = Member.Status.ACTIVE
        member.full_legal_name = "Linnea Brackenford"
        member.discord_user_id = "424242"
        member.save(update_fields=["status", "full_legal_name", "discord_user_id"])
        DiscordReconnectNudge.objects.create(member=member)
        home = f"{live_server.url}{reverse('hub_home')}"

        login_via_code(MEMBER_EMAIL)
        page.set_viewport_size(FRAMES["desktop"])
        page.goto(home)
        group = page.locator("[data-needs-attention-discord]")
        expect(group).to_be_visible()
        expect(group.get_by_role("link", name="Reconnect Discord")).to_have_attribute(
            "href", reverse("hub_discord_connect")
        )
        group.scroll_into_view_if_needed()
        shoot(page, shots / "step-1.png", target="home.discord-reconnect")
        # The phone frame and the light theme, each loaded fresh at its own size, for the PR's checks.
        page.set_viewport_size(FRAMES["phone"])
        page.goto(home)
        group.scroll_into_view_if_needed()
        shoot(page, shots / "phone-dark.png", frame="phone")
        for name, frame in (("phone-light.png", "phone"), ("desktop-light.png", "desktop")):
            page.set_viewport_size(FRAMES[frame])
            page.goto(home)
            page.evaluate("() => window.__plSetTheme && window.__plSetTheme(true)")
            group.scroll_into_view_if_needed()
            page.screenshot(path=str(shots / name))

        page.evaluate("() => { window.__plNoReload = true; }")
        group.get_by_role("button", name="Dismiss").click()
        expect(page.locator('[data-block="needs-attention"]')).to_have_count(0)
        assert page.evaluate("() => window.__plNoReload") is True  # the same document: no reload
        assert DiscordReconnectNudge.objects.get(member=member).status == DiscordReconnectNudge.Status.DISMISSED

        page.goto(home)
        expect(page.locator("[data-needs-attention-discord]")).to_have_count(0)
