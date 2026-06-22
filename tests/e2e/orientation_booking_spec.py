"""End-to-end: a signed-in member requests an orientation from a guild page.

Exercises the real browser flow — login-by-code, then booking an orientation
slot — against a live server. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from django.urls import reverse
from playwright.sync_api import expect

from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    MembershipPlanFactory,
    OrientationSlotFactory,
)

MEMBER_EMAIL = "orient-member@example.com"


def describe_orientation_booking():
    def it_lets_a_member_request_an_orientation(live_server, page, login_via_code):
        # A plan must exist so the login signal auto-creates the member the
        # orientation section is gated on (CI's fresh DB has none by default).
        MembershipPlanFactory()
        guild = GuildFactory(name="Woodshop")
        GuildOrientationSettingsFactory(guild=guild, is_enabled=True, info="Bring closed-toe shoes")
        OrientationSlotFactory(guild=guild, enabled_settings=False)

        # 1. Sign in through the genuine login-by-code flow.
        login_via_code(MEMBER_EMAIL)

        # 2. The orientation section now lives in the Schedule tab — open it.
        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[guild.pk])}")
        page.get_by_role("button", name="Schedule", exact=True).click()
        expect(page.locator("body")).to_contain_text("Get oriented for Woodshop")

        # 3. Request the open slot, then confirm in the modal.
        page.get_by_role("button", name="Request", exact=True).first.click()
        page.get_by_role("button", name="Send request", exact=True).click()

        # 4. Back on the guild page, the section now shows the pending request.
        expect(page.locator("body")).to_contain_text("awaiting confirmation")
