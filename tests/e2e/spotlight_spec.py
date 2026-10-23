"""End-to-end: the member Spotlight in a real browser (#709).

The yellow dot clearing and coming back for an edited announcement, the minimized choice
surviving a reload, the announcements rotating in Standard on their own clock (#890), the
changelog five a page inside Expanded, the #changelog-<slug> deep link landing on the right
page, and the same pager on the login page's modal. Waits are on what the page shows. Run with
``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from django.urls import reverse
from playwright.sync_api import expect

from core.factories import AnnouncementFactory
from core.models import Announcement
from plfog.version import CHANGELOG
from tests.membership.factories import MembershipPlanFactory

EMAIL = "spotlight-e2e@example.com"
STANDARD = ".hub-sidebar__spotlight [data-spotlight-state='standard']"
MINIMIZED = ".hub-sidebar__spotlight [data-spotlight-state='minimized']"
QUIET = ".hub-sidebar__spotlight [data-spotlight-state='quiet']"
ROTATOR = f"{STANDARD} [data-spotlight-announcements]"
ACTIVE = f"{ROTATOR} .pl-rotator__slide.is-active"
BANNER_ACTIVE = "[data-home-announcements] .pl-rotator__slide.is-active"


def _announcements(count: int) -> list[Announcement]:
    return [AnnouncementFactory(body=f"<p>Zorblax notice {n + 1}</p>", position=n) for n in range(count)]


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
        _announcements(1)
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
        _announcements(1)
        page.evaluate("() => localStorage.removeItem('plSpotlightMinimized')")
        _home(page, live_server)
        page.locator(f"{STANDARD} [data-spotlight-minimize]").click()
        expect(page.locator(MINIMIZED)).to_be_visible()

        _home(page, live_server)

        expect(page.locator(MINIMIZED)).to_be_visible()
        expect(page.locator(STANDARD)).to_be_hidden()

    def it_lights_the_dot_again_when_an_announcement_is_edited(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        (announcement,) = _announcements(1)
        page.evaluate("() => localStorage.removeItem('plSpotlightMinimized')")
        _home(page, live_server)
        page.locator(f"{STANDARD} [data-spotlight-minimize]").click()
        expect(page.locator(f"{MINIMIZED} [data-spotlight-dot]")).to_be_hidden()

        announcement.body = "<p>Zorblax edited</p>"
        announcement.save()
        _home(page, live_server)

        expect(page.locator(f"{MINIMIZED} [data-spotlight-dot]")).to_be_visible()
        expect(page.locator(f"{MINIMIZED} [data-spotlight-first]")).to_have_text("Zorblax edited")

    def it_rotates_the_announcements_in_standard_with_a_count(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        _announcements(3)
        page.evaluate("() => localStorage.removeItem('plSpotlightMinimized')")
        page.clock.install()
        page.goto(f"{live_server.url}{reverse('hub_beta_feedback')}")
        page.mouse.move(1200, 800)
        count = page.locator(f"{ROTATOR} [data-rotator-count]")
        expect(count).to_have_text("1 of 3")

        page.clock.run_for(8500)
        expect(count).to_have_text("2 of 3")
        expect(page.locator(ACTIVE)).to_contain_text("Zorblax notice 2")

        page.locator(f"{ROTATOR} [data-rotator-next]").click()
        expect(count).to_have_text("3 of 3")
        page.locator(f"{ROTATOR} [data-rotator-prev]").click()
        page.locator(f"{ROTATOR} [data-rotator-prev]").click()
        expect(count).to_have_text("1 of 3")

        pause = page.locator(f"{ROTATOR} [data-rotator-pause]")
        pause.click()
        expect(pause).to_have_attribute("aria-label", "Play the announcements")
        page.mouse.move(1200, 800)
        page.evaluate("() => document.activeElement.blur()")
        page.clock.run_for(30000)
        expect(count).to_have_text("1 of 3")

    def it_fits_its_controls_in_the_sidebar(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        _announcements(3)
        page.evaluate("() => localStorage.removeItem('plSpotlightMinimized')")
        _home(page, live_server)

        card = page.locator(STANDARD).bounding_box()
        assert card
        for control in ("[data-rotator-prev]", "[data-rotator-count]", "[data-rotator-next]", "[data-rotator-pause]"):
            box = page.locator(f"{ROTATOR} {control}").bounding_box()
            assert box and box["x"] >= card["x"] and box["x"] + box["width"] <= card["x"] + card["width"]
        prev = page.locator(f"{ROTATOR} [data-rotator-prev]").bounding_box()
        pause = page.locator(f"{ROTATOR} [data-rotator-pause]").bounding_box()
        assert prev and pause and abs(prev["y"] - pause["y"]) < 4

    def it_keeps_its_own_clock_beside_the_home_banner(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        _announcements(3)
        page.evaluate("() => localStorage.removeItem('plSpotlightMinimized')")
        page.clock.install()
        page.goto(f"{live_server.url}{reverse('hub_home')}")
        page.mouse.move(1200, 800)
        expect(page.locator(BANNER_ACTIVE)).to_have_attribute("aria-label", "1 of 3")

        page.locator(f"{ROTATOR} [data-rotator-pause]").click()
        page.mouse.move(1200, 800)
        page.evaluate("() => document.activeElement.blur()")
        page.clock.run_for(8500)

        expect(page.locator(BANNER_ACTIVE)).to_have_attribute("aria-label", "2 of 3")
        expect(page.locator(f"{ROTATOR} [data-rotator-count]")).to_have_text("1 of 3")

    def it_leaves_the_announcements_out_of_the_phone_copy_on_home(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        _announcements(2)
        page.set_viewport_size({"width": 390, "height": 844})
        _home(page, live_server)

        expect(page.locator("[data-home-announcements]")).to_be_visible()
        expect(page.locator("[data-spotlight-home]")).to_be_visible()
        expect(page.locator("[data-spotlight-home] [data-spotlight-announcements]")).to_have_count(0)

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


def describe_with_no_announcement_and_no_meeting():
    def it_stays_quiet_even_after_a_minimize_elsewhere(page, live_server, login_via_code):
        _sign_in(page, login_via_code)
        page.evaluate("() => localStorage.setItem('plSpotlightMinimized', '1')")

        _home(page, live_server)

        expect(page.locator(QUIET)).to_be_visible()
        expect(page.locator(STANDARD)).to_have_count(0)
        expect(page.locator(MINIMIZED)).to_have_count(0)

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
