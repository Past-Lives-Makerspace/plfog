"""End-to-end: the Orientations page's "+ Add an Orientation" button (#637) in a real browser.

A guild lead of one guild clicks the header button and lands on that guild's Orientations page
(#672). A lead of two guilds opens the menu, sees the guilds by name, and picks
one. At 375px the button and its menu stay in reach with no sideways scroll. Waits are on what
the page shows, never a snapshot after it (the boosted arrival trap). Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import Guild, Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MembershipPlanFactory

LEAD_EMAIL = "add-orientation-lead@example.com"
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


def _lead(login_via_code) -> Member:
    MembershipPlanFactory()
    login_via_code(LEAD_EMAIL)
    return get_user_model().objects.get(username=LEAD_EMAIL).member


def _expect_orientations_page(page, live_server, guild: Guild) -> None:
    expect(page).to_have_url(f"{live_server.url}{reverse('hub_guild_orientations', args=[guild.pk])}")
    expect(page.get_by_role("heading", level=1)).to_have_text(f"{guild.name} Orientations")
    expect(page.get_by_role("heading", name="Orientation Types")).to_be_visible()


def describe_the_add_orientation_button():
    def it_takes_a_lead_of_one_guild_straight_to_its_orientations_page(live_server, page, login_via_code):
        guild = GuildFactory(name="Button Woodshop", guild_lead=_lead(login_via_code))
        page.goto(f"{live_server.url}{reverse('hub_orientations')}")

        button = page.locator("[data-add-orientation]").get_by_role("link", name="+ Add an Orientation")
        expect(button).to_be_visible()
        # The header carries it on every tab, not only List.
        page.get_by_role("tab", name="Bookings").click()
        expect(button).to_be_visible()

        button.click()
        _expect_orientations_page(page, live_server, guild)

    def it_opens_a_menu_of_guilds_for_a_lead_of_several(live_server, page, login_via_code):
        lead = _lead(login_via_code)
        GuildFactory(name="Zephyr Metalshop", guild_lead=lead)
        ceramics = GuildFactory(name="Anchor Ceramics")
        GuildStaffMembershipFactory(guild=ceramics, member=lead)
        page.goto(f"{live_server.url}{reverse('hub_orientations')}")

        trigger = page.get_by_role("button", name="Add an Orientation")
        trigger.click()
        menu = page.locator("[data-add-orientation]").get_by_role("menu")
        expect(menu).to_be_visible()
        expect(menu.get_by_role("menuitem")).to_have_text(["Anchor Ceramics", "Zephyr Metalshop"])
        expect(menu.get_by_role("menuitem", name="Anchor Ceramics")).to_be_focused()
        page.keyboard.press("Escape")
        expect(menu).to_be_hidden()

        trigger.click()
        menu.get_by_role("menuitem", name="Anchor Ceramics").click()
        _expect_orientations_page(page, live_server, ceramics)

    def it_stays_in_reach_on_a_phone_with_no_sideways_scroll(live_server, page, login_via_code):
        page.set_viewport_size({"width": 375, "height": 812})
        lead = _lead(login_via_code)
        GuildFactory(name="Phone Woodshop", guild_lead=lead)
        GuildFactory(name="Phone Jewelry", guild_lead=lead)
        page.goto(f"{live_server.url}{reverse('hub_orientations')}")

        trigger = page.get_by_role("button", name="Add an Orientation")
        expect(trigger).to_be_visible()
        assert page.evaluate(NO_SIDEWAYS_SCROLL)
        trigger.click()
        menu = page.locator("[data-add-orientation]").get_by_role("menu")
        expect(menu).to_be_visible()
        box = menu.bounding_box()
        assert box is not None
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= 375
        assert page.evaluate(NO_SIDEWAYS_SCROLL)


def _admin_with_many_guilds(login_via_code, count: int = 17) -> list[Guild]:
    """Sign an admin in with ``count`` guilds, more than fit under the header on any screen."""
    member = _lead(login_via_code)
    member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["fog_role"])
    member.sync_user_permissions()
    return [GuildFactory(name=f"Long Menu Guild {index:02d}") for index in range(1, count + 1)]


def describe_a_menu_longer_than_the_screen():
    @pytest.mark.parametrize(("width", "height"), [(1366, 768), (375, 812)])
    def it_stays_inside_the_viewport_and_scrolls_to_its_last_guild(
        live_server, page, login_via_code, width: int, height: int
    ):
        page.set_viewport_size({"width": width, "height": height})
        guilds = _admin_with_many_guilds(login_via_code)
        page.goto(f"{live_server.url}{reverse('hub_orientations')}")

        page.get_by_role("button", name="Add an Orientation").click()
        menu = page.locator("[data-add-orientation]").get_by_role("menu")
        expect(menu).to_be_visible()
        expect(menu.get_by_role("menuitem")).to_have_count(len(guilds))
        box = menu.bounding_box()
        assert box is not None
        assert box["y"] >= 0
        assert box["y"] + box["height"] <= height
        assert box["x"] >= 0
        assert box["x"] + box["width"] <= width

        # A wheel over the menu scrolls the menu, not the page, so it stays open.
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.wheel(0, 4000)
        expect(menu).to_be_visible()
        page.wait_for_function("() => document.querySelector('[data-add-orientation] [role=menu]').scrollTop > 0")
        assert page.evaluate("() => window.scrollY") == 0

        last = menu.get_by_role("menuitem", name=guilds[-1].name)
        last.scroll_into_view_if_needed()
        expect(menu).to_be_visible()
        last.click()
        _expect_orientations_page(page, live_server, guilds[-1])
