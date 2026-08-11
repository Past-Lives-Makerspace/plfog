"""End-to-end: the Info View hover-help mode (Spec B).

Drives the real toggle → fetch → outline → panel → pin → Esc loop in a browser:
help mode is client-only (localStorage + one JSON fetch), so these are the only
tests that can see the actual promise — outlines appear, clicks inspect instead
of act, the mode survives boosted navigation, and a dead endpoint degrades
quietly. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import re

from django.urls import reverse
from playwright.sync_api import expect

from tests.membership.factories import GuildFactory, HelpCategoryFactory, MembershipPlanFactory, WikiArticleFactory

HELP_MODE = re.compile(r"(^|\s)pl-help-mode(\s|$)")
MEMBER_EMAIL = "info-view-member@example.com"


def _seed_member_world() -> None:
    """A plan (so login auto-creates the member) and a guild to vote on."""
    MembershipPlanFactory()
    GuildFactory(name="Fiber Arts Guild")


def describe_info_view():
    def it_toggles_help_mode_and_shows_the_hint(live_server, page, login_via_code):
        _seed_member_world()
        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_guild_voting')}")

        page.click("[data-help-toggle]")
        expect(page.locator("html")).to_have_class(HELP_MODE)
        panel = page.locator("[data-infoview-panel]")
        expect(panel).to_be_visible()
        expect(panel).to_contain_text("Hover or tap anything highlighted")

        page.keyboard.press("Escape")
        expect(page.locator("html")).not_to_have_class(HELP_MODE)
        expect(panel).to_be_hidden()

    def it_shows_a_topic_on_hover_and_pins_on_click(live_server, page, login_via_code):
        _seed_member_world()
        # Seed the article so "Read more" resolves to the real KB anchor.
        WikiArticleFactory(slug="guild-voting", category=HelpCategoryFactory(slug="guilds"), is_published=True)
        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_guild_voting')}")

        page.click("[data-help-toggle]")
        # Scoped to the page content: Spec C also stamps voting.rank-guilds on the
        # sidebar's Guild Voting link (the member tour's step-5 target), so the bare
        # selector matches two elements. The ballot card in <main> is the one under test.
        target = page.locator('main [data-help-key="voting.rank-guilds"]')
        expect(target).to_have_class(re.compile(r"(^|\s)pl-infoview-target(\s|$)"))  # outline appears post-fetch
        target.hover()

        panel = page.locator("[data-infoview-panel]")
        expect(panel).to_contain_text("Rank your top 3")
        more = panel.locator("a", has_text="Read more")
        href = more.get_attribute("href")
        assert href is not None and href.startswith("/help/") and "#" in href

        # Click pins — and must NOT act (the ballot form does not submit).
        url_before = page.url
        target.click()
        expect(target).to_have_class(re.compile(r"(^|\s)pl-infoview-pinned(\s|$)"))
        expect(panel).to_contain_text("Unpin")
        assert page.url == url_before  # no navigation, no submit

        # Esc ladder: first unpins, second exits.
        page.keyboard.press("Escape")
        expect(target).not_to_have_class(re.compile(r"(^|\s)pl-infoview-pinned(\s|$)"))
        expect(page.locator("html")).to_have_class(HELP_MODE)
        page.keyboard.press("Escape")
        expect(page.locator("html")).not_to_have_class(HELP_MODE)

    def it_survives_a_boosted_navigation(live_server, page, login_via_code):
        _seed_member_world()
        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_guild_voting')}")

        page.click("[data-help-toggle]")
        expect(page.locator("html")).to_have_class(HELP_MODE)

        # The sidebar nav opts out of hx-boost; the profile dropdown's Settings
        # link is a genuinely boosted navigation (body swap, no full load).
        settings_path = reverse("hub_user_settings")
        page.click(".pl-profile__avatar")
        page.click(f'.pl-profile__dropdown a[href="{settings_path}"]')
        page.wait_for_url(f"**{settings_path}")

        expect(page.locator("html")).to_have_class(HELP_MODE)
        expect(page.locator("[data-infoview-panel]")).to_be_visible()

    def it_fails_silent_when_topics_are_unreachable(live_server, page, login_via_code):
        _seed_member_world()
        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_guild_voting')}")
        page.route("**/help/topics.json", lambda route: route.abort())

        page.click("[data-help-toggle]")
        panel = page.locator("[data-infoview-panel]")
        expect(panel).to_contain_text("Help topics couldn’t load.")
        expect(panel.locator("[data-infoview-retry]")).to_be_visible()
        assert page.locator(".pl-infoview-target").count() == 0  # no outlines

        # The page is never held hostage by help: navigation still works.
        directory_path = reverse("hub_member_directory")
        page.click(f'.hub-sidebar a[href="{directory_path}"]')
        page.wait_for_url(f"**{directory_path}")

    def it_does_not_toggle_while_typing(live_server, page, login_via_code):
        _seed_member_world()
        login_via_code(MEMBER_EMAIL)
        page.goto(f"{live_server.url}{reverse('hub_member_directory')}")

        page.focus('input[name="q"]')
        page.keyboard.type("?")
        expect(page.locator("html")).not_to_have_class(HELP_MODE)
        expect(page.locator('input[name="q"]')).to_have_value("?")
