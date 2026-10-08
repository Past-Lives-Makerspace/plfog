"""End-to-end: the member Spotlight in a real browser (#709).

The yellow dot clearing, the minimized choice surviving a reload, voting in place from
Standard, the changelog five a page inside Expanded, the #changelog-<slug> deep link landing on
the right page, and the same pager on the login page's modal. Waits are on what the page shows.
Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from plfog.version import CHANGELOG
from tests.membership.factories import MembershipPlanFactory
from tests.polls.factories import poll_with

EMAIL = "spotlight-e2e@example.com"
STANDARD = ".hub-sidebar__spotlight [data-spotlight-state='standard']"
MINIMIZED = ".hub-sidebar__spotlight [data-spotlight-state='minimized']"


def _sign_in(page, login_via_code) -> None:
    MembershipPlanFactory()
    login_via_code(EMAIL)
    page.set_viewport_size({"width": 1280, "height": 900})


def _home(page, live_server, fragment: str = "") -> None:
    page.goto("about:blank")
    page.goto(f"{live_server.url}{reverse('hub_home')}{fragment}")


def describe_the_spotlight():
    def it_shows_the_dot_for_something_new_and_clears_it_when_opened(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        poll_with("Laser", "Lathe", question="Zorblax dot?", opens_at=timezone.now() - timedelta(hours=1))
        page.evaluate(
            "() => { localStorage.setItem('plSpotlightMinimized', '1'); localStorage.removeItem('plSpotlightSeen') }"
        )

        _home(page, live_server)
        expect(page.locator(MINIMIZED)).to_be_visible()
        expect(page.locator(STANDARD)).to_be_hidden()
        expect(page.locator(f"{MINIMIZED} [data-spotlight-dot]")).to_be_visible()

        page.locator(f"{MINIMIZED} button").click()
        expect(page.locator(STANDARD)).to_be_visible()

        page.locator(f"{STANDARD} [data-spotlight-minimize]").click()
        expect(page.locator(MINIMIZED)).to_be_visible()
        expect(page.locator(f"{MINIMIZED} [data-spotlight-dot]")).to_be_hidden()

    def it_remembers_the_minimized_choice_across_a_reload(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        _home(page, live_server)
        page.locator(f"{STANDARD} [data-spotlight-minimize]").click()
        expect(page.locator(MINIMIZED)).to_be_visible()

        _home(page, live_server)

        expect(page.locator(MINIMIZED)).to_be_visible()
        expect(page.locator(STANDARD)).to_be_hidden()

    def it_votes_in_place_from_standard(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        poll_with("Laser", "Lathe", question="Zorblax vote?", opens_at=timezone.now() - timedelta(hours=1))
        _home(page, live_server)

        page.locator(f"{STANDARD} [data-poll-choice]").first.click()

        expect(page.locator(f"{STANDARD} [data-my-vote]")).to_be_visible()
        expect(page.locator(f"{STANDARD} [data-poll-choices]")).to_have_count(0)
        expect(page).to_have_url(f"{live_server.url}{reverse('hub_home')}")

    def it_pages_the_changelog_five_at_a_time_inside_expanded(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        _home(page, live_server)

        page.locator(f"{STANDARD} [data-spotlight-details]").click()
        panel = page.locator("[data-spotlight-panel]")
        expect(panel.locator("[data-spotlight-version]")).to_be_visible()
        panel.locator("[data-spotlight-version]").click()

        entries = panel.locator("#spotlight-changelog [data-changelog-entry]:visible")
        expect(entries).to_have_count(5)
        panel.locator("[data-changelog-older]").click()
        expect(panel.locator("[data-changelog-label]")).to_contain_text("Page 2 of")
        expect(entries).to_have_count(5)
        panel.locator("[data-spotlight-back]").click()
        expect(panel.locator("[data-spotlight-version]")).to_be_visible()

    def it_opens_a_changelog_deep_link_on_the_page_holding_it(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        slug = [entry["slug"] for entry in CHANGELOG if entry.get("slug")][7]

        _home(page, live_server, f"#changelog-{slug}")

        entry = page.locator(f"#changelog-{slug}")
        expect(entry).to_be_visible()
        expect(page.locator("#spotlight-changelog [data-changelog-label]")).to_contain_text("Page 2 of")


def describe_the_plain_changelog():
    def it_pages_five_at_a_time_on_the_login_page(page, live_server):
        page.goto(f"{live_server.url}/accounts/login/")
        page.locator(".site-version-badge").first.click()

        expect(page.locator("#changelog-modal")).to_be_visible()
        expect(page.locator("#changelog-modal-pages [data-changelog-entry]:visible")).to_have_count(5)
        page.locator("#changelog-modal-pages [data-changelog-older]").click()
        expect(page.locator("#changelog-modal-pages [data-changelog-label]")).to_contain_text("Page 2 of")
