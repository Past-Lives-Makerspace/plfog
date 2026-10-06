"""Floating buttons stack above the feedback bubble on phones instead of hiding under it.

At phone widths the hub shows a yellow feedback bubble in the bottom right corner. The guild
page's floating Member / Join pill and the Settings page's back to top button used the same
corner at a lower z-index, so the bubble covered them ("✓ Mem", half a Join pill nobody could
tap). These measure the real boxes in a browser at 390px, where the collision lived, and check
the desktop placement is untouched. ``CAPTURE_FAB_SCREENSHOTS=1`` also saves the PR's picture
under ``mockups/screenshots/``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import FloatRect, Page, expect

from membership.models import Guild, Member
from tests.membership.factories import GuildFactory, GuildMembershipFactory, MemberFactory, MembershipPlanFactory

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}
MEMBER_EMAIL = "fab-stack-member@example.com"
SHOTS = Path("mockups/screenshots")
SCROLL_TO_END = "() => window.scrollTo(0, document.documentElement.scrollHeight)"

# Long enough that a phone can scroll well past the pill's 400px reveal.
LONG_ABOUT = "\n".join(f"Open studio night {n}: bring a project and a friend." for n in range(1, 61))


def _member_for(email: str) -> Member:
    user, _ = get_user_model().objects.get_or_create(username=email, defaults={"email": email})
    return Member.objects.filter(user=user).first() or cast(Member, MemberFactory(user=user))


def _guild() -> Guild:
    MembershipPlanFactory()
    return cast(Guild, GuildFactory(name="Fabstack Ceramics Guild", about=LONG_ABOUT))


def _box(page: Page, selector: str) -> FloatRect:
    box = page.locator(selector).bounding_box()
    assert box is not None, f"{selector} has no box"
    return box


def _overlaps(a: FloatRect, b: FloatRect) -> bool:
    return (
        a["x"] < b["x"] + b["width"]
        and b["x"] < a["x"] + a["width"]
        and a["y"] < b["y"] + b["height"]
        and b["y"] < a["y"] + a["height"]
    )


def _inside_viewport(box: FloatRect, viewport: dict[str, int]) -> bool:
    return (
        box["x"] >= 0
        and box["y"] >= 0
        and box["x"] + box["width"] <= viewport["width"]
        and box["y"] + box["height"] <= viewport["height"]
    )


def _assert_clear_of_bubble(page: Page, selector: str) -> None:
    bubble = page.locator(".hub-feedback-fab")
    expect(bubble).to_be_visible()
    expect(page.locator(selector)).to_be_visible()
    floating, bubble_box = _box(page, selector), _box(page, ".hub-feedback-fab")
    assert not _overlaps(floating, bubble_box), f"{selector} {floating} intersects the bubble {bubble_box}"
    assert _inside_viewport(floating, PHONE), f"{selector} {floating} runs off the screen"
    assert _inside_viewport(bubble_box, PHONE), f"bubble {bubble_box} runs off the screen"


def _capture(page: Page, name: str) -> None:
    if os.environ.get("CAPTURE_FAB_SCREENSHOTS"):
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / name), full_page=False)


def describe_floating_buttons_on_a_phone():
    def it_shows_the_whole_member_pill_above_the_bubble(live_server, page, login_via_code):
        guild = _guild()
        page.set_viewport_size(PHONE)
        login_via_code(MEMBER_EMAIL)
        GuildMembershipFactory(guild=guild, member=_member_for(MEMBER_EMAIL))

        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}")
        page.evaluate(SCROLL_TO_END)

        pill = ".pl-guild-join-fab__pill--member"
        _assert_clear_of_bubble(page, pill)
        expect(page.locator(pill)).to_contain_text("Member")
        _capture(page, "mobile-fab-stack-guild-member-390.png")

    def it_lets_a_non_member_tap_the_whole_join_pill(live_server, page, login_via_code):
        guild = _guild()
        page.set_viewport_size(PHONE)
        login_via_code(MEMBER_EMAIL)

        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}")
        page.evaluate(SCROLL_TO_END)

        pill = ".pl-guild-join-fab__pill--join"
        _assert_clear_of_bubble(page, pill)
        _capture(page, "mobile-fab-stack-guild-join-390.png")

        # The pill's right edge is where the bubble used to sit; a click there must land on it.
        box = _box(page, pill)
        page.mouse.click(box["x"] + box["width"] - 6, box["y"] + box["height"] / 2)
        expect(page.locator("#join-guild-title")).to_be_visible()

    def it_keeps_back_to_top_clear_of_the_bubble_on_settings(live_server, page, login_via_code):
        MembershipPlanFactory()
        # Enough guild rows on the Guilds tab to scroll past the button's 400px reveal.
        for n in range(1, 31):
            GuildFactory(name=f"Fabstack Guild {n}")
        page.set_viewport_size(PHONE)
        login_via_code(MEMBER_EMAIL)

        page.goto(f"{live_server.url}{reverse('hub_user_settings')}")
        page.evaluate(SCROLL_TO_END)

        _assert_clear_of_bubble(page, ".pl-scroll-top")


def describe_floating_buttons_on_desktop():
    def it_keeps_the_guild_pill_where_it_was(live_server, page, login_via_code):
        guild = _guild()
        page.set_viewport_size(DESKTOP)
        login_via_code(MEMBER_EMAIL)

        page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}")
        page.evaluate(SCROLL_TO_END)

        expect(page.locator(".hub-feedback-fab")).to_be_hidden()
        fab = page.locator(".pl-guild-join-fab")
        expect(fab).to_be_visible()
        # Today's placement: 1.25rem (20px) from the bottom and right, no safe area on desktop.
        assert fab.evaluate("el => getComputedStyle(el).bottom") == "20px"
        assert fab.evaluate("el => getComputedStyle(el).right") == "20px"
