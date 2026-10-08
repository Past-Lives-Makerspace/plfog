"""End-to-end: "+ Add an Orientation" and the Add an Orientation page (#637, #680) in a real browser.

A single guild orienter clicks the header button on ``/orientations/``, lands on the add
page with their guild already chosen, fills the form, picks Donation based, saves, and lands
on the guild's Orientations page showing the new type. An admin gets the same plain link, no
menu, and a Guild select of every active guild. At 375px, in light and dark, the page holds
together with no sideways scroll. Waits are on what the page shows, never a snapshot after it
(the boosted arrival trap). Set ``CAPTURE_680_SCREENSHOTS=1`` to write the PR screenshots to
``mockups/screenshots/``. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import Guild, GuildStaffMembership, Member, OrientationType
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MembershipPlanFactory

ORIENTER_EMAIL = "add-orientation-orienter@example.com"
ADMIN_EMAIL = "add-orientation-admin@example.com"
SHOTS = Path(__file__).resolve().parents[2] / "mockups" / "screenshots"
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


def _sign_in(login_via_code, email: str, fog_role: str = Member.FogRole.MEMBER) -> Member:
    MembershipPlanFactory()
    login_via_code(email)
    member = get_user_model().objects.get(username=email).member
    if fog_role != Member.FogRole.MEMBER:
        member.fog_role = fog_role
        member.save(update_fields=["fog_role"])
        member.sync_user_permissions()
    return member


def _orienter_of(guild_name: str, login_via_code) -> Guild:
    guild = GuildFactory(name=guild_name)
    GuildStaffMembershipFactory(
        guild=guild, member=_sign_in(login_via_code, ORIENTER_EMAIL), role=GuildStaffMembership.Role.ORIENTER
    )
    return guild


def _capture(page, name: str) -> None:
    if os.environ.get("CAPTURE_680_SCREENSHOTS"):
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / name), full_page=True)


def _open_add_page_from_orientations(page, live_server) -> None:
    page.goto(f"{live_server.url}{reverse('hub_orientations')}")
    button = page.locator("[data-add-orientation]").get_by_role("link", name="+ Add an Orientation")
    expect(button).to_be_visible()
    button.click()
    expect(page).to_have_url(f"{live_server.url}{reverse('hub_orientation_add')}")
    expect(page.get_by_role("heading", level=1)).to_have_text("Add an Orientation")


def describe_adding_an_orientation():
    def it_takes_a_single_guild_orienter_from_the_button_to_a_saved_type(live_server, page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        guild = _orienter_of("Tech Guild", login_via_code)
        _open_add_page_from_orientations(page, live_server)

        guild_select = page.get_by_label("Guild", exact=True)
        expect(guild_select).to_have_value(str(guild.pk))
        _capture(page, "680-orienter.png")

        page.get_by_label("Name", exact=True).fill("Vinyl Cutter")
        page.get_by_label("Length (minutes)", exact=True).fill("45")
        page.get_by_label("Seats per slot", exact=True).fill("2")
        page.locator("label.pl-toggle:has(#id_is_donation)").click()
        page.get_by_label("Minimum", exact=True).fill("5")
        expect(page.get_by_label("Price", exact=True)).to_be_hidden()
        page.get_by_role("button", name="Save", exact=True).click()

        expect(page).to_have_url(f"{live_server.url}{reverse('hub_guild_orientations', args=[guild.pk])}")
        expect(page.locator('#otypes-form input[value="Vinyl Cutter"]')).to_be_visible()
        created = OrientationType.objects.get(name="Vinyl Cutter")
        assert (created.guild_id, created.duration_minutes, created.default_seats) == (guild.pk, 45, 2)
        assert (created.is_donation, created.donation_minimum_cents) == (True, 500)

    def it_gives_an_admin_one_link_and_a_guild_select_of_every_active_guild(live_server, page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        _sign_in(login_via_code, ADMIN_EMAIL, Member.FogRole.ADMIN)
        names = [f"Guild {letter}" for letter in "QWERTYUIOPASDFGH"]
        for name in names:
            GuildFactory(name=name)
        _open_add_page_from_orientations(page, live_server)

        options = page.get_by_label("Guild", exact=True).locator("option")
        expect(options).to_have_text(["Choose a guild", *sorted(names)])
        expect(page.get_by_label("Guild", exact=True)).to_have_value("")
        _capture(page, "680-admin.png")

    def it_returns_to_orientations_from_cancel(live_server, page, login_via_code):
        _orienter_of("Cancel Guild", login_via_code)
        _open_add_page_from_orientations(page, live_server)
        page.get_by_role("link", name="Cancel", exact=True).click()
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_orientations')}")


def describe_on_a_phone():
    @pytest.mark.parametrize("theme", ["light", "dark"])
    def it_holds_together_with_no_sideways_scroll(live_server, page, login_via_code, theme: str):
        page.set_viewport_size({"width": 375, "height": 812})
        _orienter_of("Phone Guild", login_via_code)
        page.goto(f"{live_server.url}{reverse('hub_orientations')}")
        page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
        expect(page.locator("[data-add-orientation-link]")).to_be_visible()
        assert page.evaluate(NO_SIDEWAYS_SCROLL)

        page.goto(f"{live_server.url}{reverse('hub_orientation_add')}")
        page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
        save = page.get_by_role("button", name="Save", exact=True)
        save.scroll_into_view_if_needed()
        expect(save).to_be_in_viewport()
        assert page.evaluate(NO_SIDEWAYS_SCROLL)
