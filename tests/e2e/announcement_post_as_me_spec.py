"""End-to-end (#730): a lead turns on "Post on Discord as me" and the Discord Preview shows their name.

The switch's ``@change`` refreshes the preview in the browser, which no Django spec can see. The
Discord user lookup is mocked with ``respx`` (the live server runs in this process) and the CDN
picture is served by Playwright, so nothing leaves the machine. Screenshots of the Discord section
in both themes go to ``PL_SCREENSHOT_DIR`` when it is set. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
import respx
from django.core.cache import cache
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import Member
from tests.membership.factories import GuildFactory, MembershipPlanFactory

LEAD_EMAIL = "kiln-lead@example.com"
EDITOR = '.pl-rte[data-rte-for="id_body"] .ql-editor'
DISCORD_ID = "730730"
AVATAR_FILE = Path(__file__).resolve().parents[2] / "static" / "img" / "guild_logos" / "ceramics_color.svg"


def _screenshot(page, theme: str) -> None:
    """The Discord section in ``theme``, saved only when ``PL_SCREENSHOT_DIR`` names a folder."""
    folder = os.environ.get("PL_SCREENSHOT_DIR")
    if not folder:
        return
    page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
    page.set_viewport_size({"width": 1100, "height": 2400})
    page.locator(".pl-compose-channel:has(#compose-discord-preview)").screenshot(
        path=str(Path(folder) / f"730-post-as-me-{theme}.png")
    )


def describe_posting_on_discord_as_me():
    def it_shows_the_leads_discord_name_and_picture_once_switched_on(live_server, page, login_via_code, settings):
        settings.DISCORD_BOT_TOKEN = "bot-tok"
        settings.IS_STAGING = False
        cache.clear()
        MembershipPlanFactory()
        guild = GuildFactory(
            name="Ceramics Guild", discord_webhook_url="https://discord.com/api/webhooks/7/c", discord_post_enabled=True
        )
        login_via_code(LEAD_EMAIL)
        lead = Member.objects.get(user__username=LEAD_EMAIL)
        lead.discord_user_id = DISCORD_ID
        lead.save(update_fields=["discord_user_id"])
        guild.guild_lead = lead
        guild.save(update_fields=["guild_lead"])
        page.route(
            "https://cdn.discordapp.com/**",
            lambda route: route.fulfill(path=str(AVATAR_FILE), content_type="image/svg+xml"),
        )

        with respx.mock(assert_all_called=False, assert_all_mocked=False) as router:
            lookup = router.get(f"https://discord.com/api/v10/users/{DISCORD_ID}").mock(
                return_value=httpx.Response(200, json={"id": DISCORD_ID, "global_name": "Felix Plaza", "avatar": "f1"})
            )
            page.goto(f"{live_server.url}{reverse('hub_compose')}?audience=guild:{guild.pk}")
            page.locator(EDITOR).fill("Glaze day is Saturday.")
            page.get_by_role("button", name="Preview & send").click()

            sender = page.locator("[data-discord-preview-sender]")
            expect(sender).to_have_text("Past Lives Makerspace")
            switch = page.locator("[data-compose-post-as-me]")
            expect(switch).to_be_visible()
            expect(switch.locator("input[name=discord_post_as_me]")).not_to_be_checked()

            switch.locator(".pl-toggle__slider").click()
            expect(sender).to_have_text("Felix Plaza")
            expect(page.locator("[data-discord-preview-avatar]")).to_be_visible()
            assert lookup.call_count == 1

            _screenshot(page, "dark")
            _screenshot(page, "light")
            page.evaluate("() => document.documentElement.removeAttribute('data-theme')")

            # Choosing "Don't post" hides the switch: there is no Discord post to sign.
            page.locator("select[name=discord_channel]").select_option("none")
            expect(switch).to_be_hidden()

            # Switching it off again puts the app name back.
            page.locator("select[name=discord_channel]").select_option("guild")
            switch.locator(".pl-toggle__slider").click()
            expect(sender).to_have_text("Past Lives Makerspace")
        cache.clear()
