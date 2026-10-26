"""End-to-end: the member Spotlight in a real browser (#709): the static version card since #935.

The card stays the same with Spotlight messages live and with an old minimized choice in storage, Details opens with the
latest update and no messages, the changelog pages five at a time inside it, the #changelog-<slug> deep link lands on the
right page, and the login page's modal uses the same pager. Waits are on what the page shows. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from django.urls import reverse
from playwright.sync_api import expect

from core.factories import AnnouncementFactory
from plfog.version import CHANGELOG
from tests.membership.factories import MembershipPlanFactory

EMAIL = "spotlight-e2e@example.com"
QUIET = ".hub-sidebar__spotlight [data-spotlight-state='quiet']"


def _sign_in(page, login_via_code) -> None:
    MembershipPlanFactory()
    login_via_code(EMAIL)
    page.set_viewport_size({"width": 1280, "height": 900})


def _home(page, live_server, fragment: str = "") -> None:
    page.goto("about:blank")
    page.goto(f"{live_server.url}{reverse('hub_home')}{fragment}")


def describe_the_version_card():
    def it_stays_the_same_card_with_messages_live_and_an_old_minimized_choice(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        AnnouncementFactory(body="<p>Zorblax live notice</p>")
        page.evaluate("() => localStorage.setItem('plSpotlightMinimized', '1')")

        page.goto(f"{live_server.url}{reverse('hub_beta_feedback')}")

        expect(page.locator(QUIET)).to_be_visible()
        expect(page.locator(".hub-sidebar__spotlight")).not_to_contain_text("Zorblax live notice")
        expect(page.locator(".hub-sidebar__spotlight [data-spotlight-state]")).to_have_count(1)
        assert page.evaluate("() => document.documentElement.dataset.spotlightMin === undefined")

    def it_opens_details_with_the_latest_update_and_no_messages(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        AnnouncementFactory(body="<p>Zorblax panel notice</p>")
        _home(page, live_server)

        page.locator(f"{QUIET} [data-spotlight-details]").click()

        panel = page.locator("[data-spotlight-panel]")
        expect(panel.locator("[data-spotlight-panel-update]")).to_contain_text(CHANGELOG[0]["title"])
        expect(panel.locator("[data-spotlight-suggest]")).to_be_visible()
        expect(panel).not_to_contain_text("Zorblax panel notice")

    def it_pages_the_changelog_five_at_a_time_inside_expanded(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        _home(page, live_server)

        page.locator(f"{QUIET} [data-spotlight-details]").click()
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

    def it_opens_the_changelog_from_the_version_pill(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        slug = CHANGELOG[0]["slug"]
        _home(page, live_server)

        page.locator(f"{QUIET} [data-spotlight-quiet-version]").click()

        expect(page.locator("[data-spotlight-panel-changelog]")).to_be_visible()
        expect(page.locator(f"#changelog-{slug}")).to_be_visible()

    def it_keeps_the_update_date_clear_of_details(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        _home(page, live_server)
        expect(page.locator(QUIET)).to_be_visible()
        page.evaluate("document.fonts.ready.then(() => true)")

        date = page.locator(f"{QUIET} [data-spotlight-quiet-date]").bounding_box()
        details = page.locator(f"{QUIET} .pl-spotlight__quiet-details").bounding_box()
        assert date and details
        assert date["x"] + date["width"] + 8 <= details["x"]


def describe_the_plain_changelog():
    def it_pages_five_at_a_time_on_the_login_page(page, live_server):
        page.goto(f"{live_server.url}/accounts/login/")
        page.locator(".site-version-badge").first.click()

        expect(page.locator("#changelog-modal")).to_be_visible()
        expect(page.locator("#changelog-modal-pages [data-changelog-entry]:visible")).to_have_count(5)
        page.locator("#changelog-modal-pages [data-changelog-older]").click()
        expect(page.locator("#changelog-modal-pages [data-changelog-label]")).to_contain_text("Page 2 of")
