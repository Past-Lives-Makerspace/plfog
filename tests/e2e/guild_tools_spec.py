"""End-to-end: Guild Tools (#921) in a real browser.

What a Django client cannot prove: the sidebar entry a guild lead clicks says Guild Tools and lands
on the page under that name, one card in each of its sections opens its page, the picker swaps the
guild section's cards for the guild it names, an admin's entry still says Admin Tools, and a plain
member has no entry at all.

Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import Page, expect

from membership.models import Member
from tests.membership.factories import GuildFactory, GuildStaffMembershipFactory, MembershipPlanFactory


def _sign_in(login_via_code, email: str) -> Member:
    MembershipPlanFactory()  # the login signal auto-creates the member only when a plan exists
    login_via_code(email)
    return get_user_model().objects.get(username=email).member


def _sidebar(page: Page):
    return page.locator("nav.hub-sidebar__nav")


def describe_guild_tools_in_the_browser():
    def it_takes_a_guild_lead_from_the_sidebar_through_one_card_per_section(live_server, page, login_via_code):
        member = _sign_in(login_via_code, "guild-tools-lead@example.com")
        led = GuildFactory(name="Aa Browser Guild", guild_lead=member)
        staffed = GuildFactory(name="Bb Browser Guild")
        GuildStaffMembershipFactory(guild=staffed, member=member)

        page.goto(f"{live_server.url}{reverse('hub_home')}")
        _sidebar(page).get_by_role("link", name="Guild Tools", exact=True).click()
        expect(page).to_have_url(re.compile(re.escape(reverse("hub_admin_tools")) + "$"))
        expect(page.get_by_role("heading", name="Guild Tools", level=1)).to_be_visible()
        expect(_sidebar(page).get_by_role("link", name="Admin Tools", exact=True)).to_have_count(0)

        guild_section = page.locator('[data-tools-section="guild"]')
        expect(guild_section.get_by_role("heading", name="Aa Browser Guild")).to_be_visible()

        for section, title, url in (
            ("guild", "Guild Settings", reverse("hub_guild_edit", args=[led.pk])),
            ("tools", "Email Members", reverse("hub_announcements")),
            ("reports", "Orientation Bookings", reverse("hub_orientations") + "?view=bookings"),
        ):
            page.locator(f'[data-tools-section="{section}"] .pl-tool-card', has_text=title).click()
            expect(page).to_have_url(re.compile(re.escape(url) + "$"))
            page.goto(f"{live_server.url}{reverse('hub_admin_tools')}")

        page.locator("[data-tools-guild-picker] select").select_option(str(staffed.pk))
        expect(page).to_have_url(re.compile(rf"\?guild={staffed.pk}$"))
        expect(guild_section.get_by_role("heading", name="Bb Browser Guild")).to_be_visible()
        expect(guild_section.locator(".pl-tool-card", has_text="Guild Settings")).to_have_attribute(
            "href", reverse("hub_guild_edit", args=[staffed.pk])
        )

    def it_keeps_admin_tools_for_an_admin(live_server, page, login_via_code):
        _sign_in(login_via_code, "guild-tools-admin@example.com")
        user = get_user_model().objects.get(username="guild-tools-admin@example.com")
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])

        page.goto(f"{live_server.url}{reverse('hub_home')}")
        _sidebar(page).get_by_role("link", name="Admin Tools", exact=True).click()
        expect(page.get_by_role("heading", name="Admin Tools", level=1)).to_be_visible()
        expect(page.locator('[data-tools-section="guild"]')).to_have_count(0)

    def it_shows_a_plain_member_no_entry(live_server, page, login_via_code):
        _sign_in(login_via_code, "guild-tools-member@example.com")

        page.goto(f"{live_server.url}{reverse('hub_home')}")
        expect(_sidebar(page).get_by_role("link", name="Home", exact=True)).to_be_visible()
        expect(_sidebar(page).locator(f'a[href="{reverse("hub_admin_tools")}"]')).to_have_count(0)
