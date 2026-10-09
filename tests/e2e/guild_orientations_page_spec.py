"""End-to-end: the guild's Orientations page (#672) in a real browser.

A lead opens "<Guild> Orientations" and the Orientations card's one "Add New Orientation +"
opens the Add an Orientation page (#680, #732); on a hidden guild that page does not offer, an
admin's click adds a row in place, scrolled into view with the cursor in it, and the autosave
creates the orientation. The old Guild Settings link (``?tab=orientations``) lands on the page, and Guild
Settings' Orientations tab is a link there. An orienter on staff sees only their own Edit
Hours row. The page holds together at 375px and in the dark theme. Set
``CAPTURE_672_SCREENSHOTS=1`` to write the PR screenshots to ``mockups/screenshots/``. Run
with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from membership.models import Guild, GuildStaffMembership, Member, OrientationType
from tests.membership.factories import (
    GuildFactory,
    GuildOrientationSettingsFactory,
    GuildStaffMembershipFactory,
    MemberFactory,
    MembershipPlanFactory,
    OrientationAvailabilityFactory,
    OrientationSlotFactory,
    OrientationTypeFactory,
)

LEAD_EMAIL = "orientations-page-lead@example.com"
ORIENTER_EMAIL = "orientations-page-orienter@example.com"
SHOTS = Path(__file__).resolve().parents[2] / "mockups" / "screenshots"
ALPINE_READY = "() => !!(document.querySelector('[data-guild-autosave]') || {})._x_dataStack"
NO_SIDEWAYS_SCROLL = "() => document.documentElement.scrollWidth <= window.innerWidth"


def _member(login_via_code, email: str, name: str) -> Member:
    MembershipPlanFactory()
    login_via_code(email)
    member = get_user_model().objects.get(username=email).member
    member.full_legal_name = name
    member.save(update_fields=["full_legal_name"])
    return member


def _tech_guild(lead: Member) -> Guild:
    """A guild with a live laser orientation, the lead's weekly hours and one upcoming slot."""
    guild = GuildFactory(name="Tech Guild", guild_lead=lead)
    GuildOrientationSettingsFactory(guild=guild, is_enabled=True)
    laser = OrientationTypeFactory(guild=guild, name="Laser Basics")
    OrientationAvailabilityFactory(guild=guild, orientation_type=laser, orienter=lead)
    OrientationSlotFactory(guild=guild, orientation_type=laser, orienter=lead)
    return guild


def _open(page, live_server, guild: Guild) -> None:
    page.goto(f"{live_server.url}{reverse('hub_guild_orientations', args=[guild.pk])}")
    page.wait_for_function(ALPINE_READY)


def _capture(page, name: str) -> None:
    if os.environ.get("CAPTURE_672_SCREENSHOTS"):
        SHOTS.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOTS / name), full_page=True)


def describe_the_orientations_page():
    def it_adds_a_type_from_the_card_and_autosaves_it(live_server, page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        guild = _tech_guild(_member(login_via_code, LEAD_EMAIL, "Lena Lead"))
        sam = MemberFactory(full_legal_name="Sam Orienter")
        GuildStaffMembershipFactory(guild=guild, member=sam, role=GuildStaffMembership.Role.ORIENTER)
        _open(page, live_server, guild)
        expect(page.get_by_role("heading", level=1)).to_have_text("Tech Guild Orientations")
        # A lead sees every orienter's Edit Hours row: their own and Sam's.
        expect(page.locator(".pl-orient-overview__group")).to_have_count(2)
        _capture(page, "672-lead.png")
        # The page's one add sits at the top right of the Orientations card (#732).
        add = page.locator("#otypes-form [data-add-orientation-type]")
        expect(page.locator("[data-add-orientation-type]")).to_have_count(1)
        expect(add).to_have_attribute("href", f"{reverse('hub_orientation_add')}?guild={guild.pk}")
        add.click()
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_orientation_add')}?guild={guild.pk}")

    def it_adds_a_row_in_place_on_a_hidden_guild_and_autosaves_it(live_server, page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        # An admin's add page lists active guilds only, so on a hidden guild the card's add adds a row (#680, #732).
        lead = _member(login_via_code, LEAD_EMAIL, "Lena Lead")
        lead.user.is_staff = True
        lead.user.is_superuser = True
        lead.user.save(update_fields=["is_staff", "is_superuser"])
        guild = _tech_guild(lead)
        guild.is_active = False
        guild.save(update_fields=["is_active"])
        _open(page, live_server, guild)

        page.locator("#otypes-form [data-formset-add]").click()
        name = page.locator('input[name="otypes-1-name"]')
        expect(name).to_be_focused()
        expect(name).to_be_in_viewport()
        name.fill("Vinyl Cutter")
        page.locator('input[name="otypes-1-duration_minutes"]').fill("45")
        expect(page.locator('input[name="otypes-1-id"]')).not_to_have_value("")
        assert OrientationType.objects.filter(guild=guild, name="Vinyl Cutter").exists()

    def it_answers_the_old_tab_link_and_the_settings_tab_with_the_page(live_server, page, login_via_code):
        guild = _tech_guild(_member(login_via_code, LEAD_EMAIL, "Lena Lead"))
        page_url = f"{live_server.url}{reverse('hub_guild_orientations', args=[guild.pk])}"
        page.goto(f"{live_server.url}{reverse('hub_guild_edit', args=[guild.pk])}?tab=orientations")
        expect(page).to_have_url(page_url)

        page.get_by_role("link", name="Back to Tech Guild Settings").click()
        expect(page.get_by_role("heading", level=1)).to_have_text("Tech Guild Settings")
        # The link reads as one of the tabs: same colour, weight and no underline as its neighbour.
        tab_style = "(el) => { const s = getComputedStyle(el); return [s.color, s.fontSize, s.textDecorationLine, s.paddingTop]; }"
        link = page.locator("[data-orientations-tab-link]")
        neighbour = page.get_by_role("button", name="Reservations", exact=True)
        assert link.evaluate(tab_style) == neighbour.evaluate(tab_style)
        link.click()
        expect(page).to_have_url(page_url)
        expect(page.get_by_role("heading", name="Orientations", exact=True)).to_be_visible()

    def it_shows_an_orienter_only_their_own_hours(live_server, page, login_via_code):
        page.set_viewport_size({"width": 1280, "height": 900})
        orienter = _member(login_via_code, ORIENTER_EMAIL, "Omar Orienter")
        guild = _tech_guild(MemberFactory(full_legal_name="Lead Person"))
        GuildStaffMembershipFactory(guild=guild, member=orienter, role=GuildStaffMembership.Role.ORIENTER)
        _open(page, live_server, guild)
        expect(page.get_by_role("heading", level=1)).to_have_text("Tech Guild Orientations")
        expect(page.locator("[data-add-orientation-type]")).to_be_visible()
        rows = page.locator(".pl-orient-overview__group")
        expect(rows).to_have_count(1)
        expect(rows.first).not_to_contain_text("Lead Person")
        _capture(page, "672-orienter.png")

    def it_holds_together_on_a_phone_in_both_themes(live_server, page, login_via_code):
        page.set_viewport_size({"width": 375, "height": 812})
        guild = _tech_guild(_member(login_via_code, LEAD_EMAIL, "Lena Lead"))
        _open(page, live_server, guild)
        for theme in ("dark", "light"):
            page.evaluate("(t) => document.documentElement.setAttribute('data-theme', t)", theme)
            expect(page.locator("[data-add-orientation-type]")).to_be_visible()
            assert page.evaluate(NO_SIDEWAYS_SCROLL), theme
