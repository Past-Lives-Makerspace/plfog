"""End-to-end (#1063): a guild announcement with every channel off posts to the guild page only.

A lead turns Push notification, Email and Post to Discord off; step 2, the send button and the
Ready to Send? modal switch to the guild page wording, and turning one channel back on restores
today's. The send lands the post on the guild page and rings nobody's bell. Screenshots of step 2
and the modal go to ``PL_SCREENSHOT_DIR`` when it is set. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from core.models import Notification
from membership.models import GuildAnnouncement, Member
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MembershipPlanFactory

LEAD_EMAIL = "quill-lead@example.com"
EDITOR = '.pl-rte[data-rte-for="id_body"] .ql-editor'
PAGE_ONLY_LINE = "Posting to the guild page only. Nobody is notified."
PAGE_ONLY_CONFIRM = "This will post it to the top of the guild page right now. Nobody is notified."


def _screenshot(page, name: str) -> None:
    """The page as it is, saved only when ``PL_SCREENSHOT_DIR`` names a folder."""
    folder = os.environ.get("PL_SCREENSHOT_DIR")
    if folder:
        page.screenshot(path=str(Path(folder) / f"1063-{name}.png"))


def _toggle(page, name: str) -> None:
    page.locator(f"label.pl-toggle:has(input[name={name}])").click()


def describe_a_guild_page_only_announcement():
    def it_switches_the_wording_and_posts_to_the_page_without_telling_anyone(live_server, page, login_via_code):
        MembershipPlanFactory()
        guild = GuildFactory(name="Quill Guild")
        reader_user = get_user_model().objects.create_user(username="reader@example.com", email="reader@example.com")
        reader = Member.objects.get(user=reader_user)
        reader.status = Member.Status.ACTIVE
        reader.save(update_fields=["status"])
        GuildMembershipFactory(guild=guild, member=reader)

        login_via_code(LEAD_EMAIL)
        guild.guild_lead = Member.objects.get(user__username=LEAD_EMAIL)
        guild.save(update_fields=["guild_lead"])

        page.goto(f"{live_server.url}{reverse('hub_compose')}?audience=guild:{guild.pk}")
        page.locator(EDITOR).fill("Zorbly ink is on the top shelf now.")
        for name in ("push_enabled", "send_email", "discord_enabled"):
            _toggle(page, name)

        page.get_by_role("tab", name="2. Preview & send").click()
        page_only = page.locator("[data-compose-guild-page-only]")
        expect(page_only).to_have_text(PAGE_ONLY_LINE)
        expect(page.locator(".pl-wizard-reach", has_text="Reaching")).to_be_hidden()
        post_button = page.get_by_role("button", name="Post to guild page", exact=True)
        expect(post_button).to_be_visible()
        expect(page.get_by_role("button", name="Send email", exact=True)).to_be_hidden()
        _screenshot(page, "step-2")

        # Switching one channel back on restores today's wording.
        page.get_by_role("tab", name="1. Compose").click()
        _toggle(page, "push_enabled")
        page.get_by_role("tab", name="2. Preview & send").click()
        expect(page_only).to_be_hidden()
        expect(page.locator(".pl-wizard-reach", has_text="Reaching")).to_be_visible()
        expect(page.get_by_role("button", name="Send email", exact=True)).to_be_visible()
        expect(post_button).to_be_hidden()
        page.get_by_role("tab", name="1. Compose").click()
        _toggle(page, "push_enabled")
        page.get_by_role("tab", name="2. Preview & send").click()

        post_button.click()
        confirm = page.locator("[data-compose-guild-page-only-confirm]")
        expect(confirm).to_have_text(PAGE_ONLY_CONFIRM)
        _screenshot(page, "modal")
        with page.expect_navigation():
            page.get_by_role("button", name="Yes, send it").click()
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_announcements')}?tab=sent")

        post = GuildAnnouncement.objects.get()
        assert post.guild == guild
        assert not Notification.objects.exists()
        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}")
        expect(page.get_by_text("Zorbly ink is on the top shelf now.").first).to_be_visible()
