"""End-to-end: a hidden guild in the sidebar of the admin who runs it.

An admin signs in and finds the hidden Music Guild in the sidebar's Guilds list, tagged Hidden,
beside an active guild with no tag; following it opens the guild page. On a phone the opened
sidebar holds the tag on the guild's row with no sideways scroll, in light and dark. Waits are on
what the page shows. Set ``CAPTURE_HIDDEN_GUILD_SCREENSHOTS=1`` to write the PR screenshots to
``mockups/screenshots/``. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import Member
from tests.features import turn_on
from tests.membership.factories import GuildFactory, MembershipPlanFactory

ADMIN_EMAIL = "hidden-guild-sidebar-admin@example.com"
SHOTS = Path(__file__).resolve().parents[2] / "mockups" / "screenshots"
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


def _admin_with_guilds(login_via_code) -> None:
    MembershipPlanFactory()
    turn_on("guilds")
    GuildFactory(name="Music Guild", is_active=False)
    GuildFactory(name="Woodworking Guild")
    login_via_code(ADMIN_EMAIL)
    member = get_user_model().objects.get(username=ADMIN_EMAIL).member
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()


def _capture(page, name: str) -> None:
    if os.environ.get("CAPTURE_HIDDEN_GUILD_SCREENSHOTS"):
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.locator(".hub-sidebar").screenshot(path=str(SHOTS / name))


def describe_a_hidden_guild_in_the_sidebar():
    @pytest.mark.parametrize("theme", ["dark", "light"])
    def it_is_tagged_hidden_and_opens_its_page(live_server, page, login_via_code, theme: str):
        _admin_with_guilds(login_via_code)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server.url}{reverse('hub_home')}")
        page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
        nav = page.get_by_role("navigation", name="Hub navigation")
        music = nav.get_by_role("link", name="Music Guild")
        expect(music.locator(".hub-sidebar__private")).to_have_text("Hidden")
        expect(nav.get_by_role("link", name="Woodworking Guild").locator(".hub-sidebar__private")).to_have_count(0)
        music.scroll_into_view_if_needed()
        _capture(page, f"hidden-guild-sidebar-desktop-{theme}.png")
        music.click()
        expect(page).to_have_url(f"{live_server.url}/guilds/music-guild/")
        expect(page.get_by_role("heading", name="Music Guild").first).to_be_visible()

    @pytest.mark.parametrize("theme", ["dark", "light"])
    def it_holds_together_on_a_phone(live_server, page, login_via_code, theme: str):
        _admin_with_guilds(login_via_code)
        page.set_viewport_size({"width": 375, "height": 812})
        page.goto(f"{live_server.url}{reverse('hub_home')}")
        page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
        page.get_by_role("button", name="Toggle sidebar").click()
        tag = (
            page.get_by_role("navigation", name="Hub navigation")
            .get_by_role("link", name="Music Guild")
            .locator(".hub-sidebar__private")
        )
        tag.scroll_into_view_if_needed()
        expect(tag).to_be_visible()
        assert page.evaluate(NO_SIDEWAYS_SCROLL)
        _capture(page, f"hidden-guild-sidebar-phone-{theme}.png")
