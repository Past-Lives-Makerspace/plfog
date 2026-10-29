"""New! badges and the per feature What's New button in a real browser (#1007, part 3).

A member with an unseen Cubbies entry sees New! on the guild page's Cubbies tab; the tab click
posts the key seen and the badge leaves in place, and it stays gone on a reload. At phone width
the section picker carries a dot on its summary and the badge in its list, and a choice there
clears both. The statbar's sparkles button opens What's New filtered to the guild. Admin Tools
carries the admin button for an admin and Guild Tools the guild one for a lead, never the other.
The changelog is patched for the live server (same process), so the spec depends on neither the
repo's fragments nor the calendar. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import expect

from membership.models import Member
from tests.membership.factories import CubbyFactory, GuildFactory, MembershipPlanFactory, ShelfUnitFactory

EMAIL = "new-badges@example.com"
PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}
SUMMARY = ".pl-tab-picker__summary"
MENU = ".pl-tab-picker__menu"
CUBBIES_BADGE = '.pl-new[data-new-key="guild.cubbies"]'


def _entries() -> list[dict[str, Any]]:
    today = timezone.localdate().isoformat()
    return [
        {
            "date": today,
            "title": "E2E cubbies entry",
            "changes": ["Rent a cubby from the Cubbies tab."],
            "slug": "e2e-cubbies",
            "audiences": ["members", "guild-staff"],
            "feature": "guild.cubbies",
            "guidance": "link",
        },
        {
            "date": today,
            "title": "E2E reconciliation entry",
            "changes": ["Owed now is what to pay by hand."],
            "slug": "e2e-recon",
            "audiences": ["admins"],
            "feature": "admin.reconciliation",
            "guidance": "link",
        },
    ]


@pytest.fixture(autouse=True)
def _patched_changelog(monkeypatch):
    monkeypatch.setattr("plfog.version.CHANGELOG", _entries())


@pytest.fixture(autouse=True)
def _settle_before_the_database_is_truncated(page, live_server, transactional_db):
    """Let the seen POST and the page's requests land before teardown truncates."""
    yield
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except PlaywrightError:
        pass


def _member(*, admin: bool = False) -> Member:
    MembershipPlanFactory()
    member = get_user_model().objects.create_user(username=EMAIL, email=EMAIL).member
    member.status = Member.Status.ACTIVE
    member.welcome_dismissed_at = timezone.now()
    member.guided_tours_enabled = False
    if admin:
        member.fog_role = Member.FogRole.ADMIN
    member.save(update_fields=["status", "welcome_dismissed_at", "guided_tours_enabled", "fog_role"])
    member.sync_user_permissions()
    return member


def _cubbies_guild(**fields: Any):
    guild = GuildFactory(name="Thornfield Ceramics", show_cubbies_tab=True, **fields)
    CubbyFactory(unit=ShelfUnitFactory(guild=guild, name="Glazewick Wall", rows=1, columns=2), label="W1")
    return guild


def _open_guild(page: Any, live_server: Any, login_via_code: Any, size: dict[str, int]):
    _member()
    guild = _cubbies_guild()
    page.set_viewport_size(size)
    login_via_code(EMAIL)
    page.goto(f"{live_server.url}{reverse('hub_guild_detail', args=[guild.slug])}")
    return guild


def describe_the_badge():
    def it_leaves_on_the_tab_click_and_stays_gone_on_a_reload(page, live_server, login_via_code):
        _open_guild(page, live_server, login_via_code, DESKTOP)
        tab = page.locator('[data-guild-tab="cubbies"]')
        badge = tab.locator(CUBBIES_BADGE)
        expect(badge).to_be_visible()
        expect(badge).to_have_text("New!")

        with page.expect_response(
            lambda r: "/whats-new/seen/guild.cubbies/" in r.url and r.request.method == "POST"
        ) as seen:
            tab.click()
        assert seen.value.status == 204
        expect(page.locator(CUBBIES_BADGE)).to_have_count(0)
        expect(page.locator("[x-show=\"section === 'cubbies'\"]")).to_be_visible()

        page.reload()
        expect(page.locator('[data-guild-tab="cubbies"]')).to_be_visible()
        expect(page.locator(CUBBIES_BADGE)).to_have_count(0)
        assert Member.objects.get(user__username=EMAIL).whats_new_seen == ["e2e-cubbies"]

    def it_dots_the_phone_pickers_summary_and_lists_the_badge_until_the_section_is_chosen(
        page, live_server, login_via_code
    ):
        _open_guild(page, live_server, login_via_code, PHONE)
        expect(page.locator("nav.pl-tabs[role=tablist]")).to_be_hidden()
        dot = page.locator(f"{SUMMARY} [data-tab-picker-dot]")
        expect(dot).to_be_visible()

        page.locator(SUMMARY).click()
        item = page.locator(f"{MENU} .pl-tab-picker__item", has_text="Cubbies")
        expect(item.locator(".pl-new")).to_have_text("New!")
        expect(page.locator(f"{MENU} .pl-new")).to_have_count(1)
        with page.expect_response(lambda r: "/whats-new/seen/guild.cubbies/" in r.url and r.request.method == "POST"):
            item.click()

        expect(page.locator(MENU)).to_be_hidden()
        expect(page.locator(".pl-tab-picker__current")).to_have_text("Cubbies")
        expect(dot).to_be_hidden()
        page.locator(SUMMARY).click()
        expect(page.locator(f"{MENU} .pl-new")).to_have_count(0)


def describe_the_button():
    def it_opens_whats_new_filtered_to_the_guild_from_the_statbar(page, live_server, login_via_code):
        _open_guild(page, live_server, login_via_code, DESKTOP)
        button = page.locator('[data-whats-new-button][data-whats-new-key="guild"]')
        expect(button).to_be_visible()
        expect(button).to_have_class(re.compile(r"pl-whats-new-btn--unseen"))
        expect(button).to_have_attribute("aria-label", "What's new in Guild")

        button.click()
        body = page.locator("#whats-new-body")
        expect(body.locator('[data-whats-new-entry="e2e-cubbies"]')).to_be_visible()
        expect(page.locator("#whats-new-title")).to_have_text("What's New · Guild")
        expect(body.locator('[data-whats-new-tab="members"]')).to_contain_text("(1)")
        expect(body.locator('[data-whats-new-entry="e2e-recon"]')).to_have_count(0)

    def it_is_the_admin_button_on_admin_tools_and_the_guild_one_on_guild_tools(page, live_server, login_via_code):
        _member(admin=True)
        page.set_viewport_size(DESKTOP)
        login_via_code(EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_admin_tools')}")
        expect(page.locator("[data-tools-page-title]")).to_have_text("Admin Tools")
        expect(page.locator('[data-whats-new-key="admin"]')).to_be_visible()
        expect(page.locator('[data-whats-new-key="guild"]')).to_have_count(0)
        expect(page.locator('.pl-tool-card .pl-new[data-new-key="admin.reconciliation"]')).to_be_visible()

        # The same person as the lead of a guild, previewing below admin, is on Guild Tools.
        member = Member.objects.get(user__username=EMAIL)
        member.fog_role = Member.FogRole.MEMBER
        member.save(update_fields=["fog_role"])
        member.sync_user_permissions()
        _cubbies_guild(guild_lead=member)
        page.goto(f"{live_server.url}{reverse('hub_admin_tools')}")
        expect(page.locator("[data-tools-page-title]")).to_have_text("Guild Tools")
        expect(page.locator('[data-whats-new-key="guild"]')).to_be_visible()
        expect(page.locator('[data-whats-new-key="admin"]')).to_have_count(0)
