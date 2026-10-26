"""Home's Upcoming queue moves in a real browser (#937, static/js/pl-upcoming-queue.js).

The first item is the featured card and the rest are the rows under it. Every 6 seconds the next item moves into the
card and the one that was featured drops to the bottom of the list. Pointing at it, keyboard focus in it or pausing it
holds it; the arrows move it either way; under prefers-reduced-motion nothing moves by itself. Driven on Playwright's
fake clock, so no spec waits real seconds. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from core.models import TourState
from membership.models import Member
from tests.membership.factories import CommunityEventFactory, MembershipPlanFactory

QUEUE = "[data-home-upcoming]"
FEATURED = f"{QUEUE} .pl-upnext__panel.is-active"
TITLES = ["Queue event A", "Queue event B", "Queue event C", "Queue event D"]


def _seed() -> None:
    for n, title in enumerate(TITLES):
        start = timezone.now() + timedelta(days=n + 1)
        CommunityEventFactory(community=True, title=title, starts_at=start, ends_at=start + timedelta(hours=2))


def _sign_in(login_via_code, email: str) -> None:
    MembershipPlanFactory()
    login_via_code(email)
    user = get_user_model().objects.get(username=email)
    member = Member.objects.get(user=user)
    member.welcome_dismissed_at = timezone.now()
    member.save(update_fields=["welcome_dismissed_at"])
    TourState.objects.mark_completed(user, "member-welcome")


def _open_home(page, live_server, login_via_code, email: str) -> None:
    _seed()
    _sign_in(login_via_code, email)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.clock.install()
    page.goto(f"{live_server.url}{reverse('hub_home')}")
    _rest(page)
    expect(page.locator(f"{QUEUE} [data-queue-next]")).to_have_count(1)


def _rest(page) -> None:
    """No mouse on the queue and no focus in it, so only a pause can hold it."""
    page.mouse.move(1430, 990)
    page.evaluate("() => document.activeElement && document.activeElement.blur()")


def _rest_mouse(page) -> None:
    """The mouse off the queue, leaving focus where it is."""
    page.mouse.move(1430, 990)


def _rows(page) -> list[str]:
    """The titles of the list's shown rows, top to bottom."""
    return page.eval_on_selector_all(
        f"{QUEUE} [data-queue-list] > [data-queue-row]:not([hidden])",
        "rows => rows.map((row) => row.querySelector('.pl-upcoming__title').textContent.trim())",
    )


def _expect_queue(page, featured: str, rows: list[str]) -> None:
    expect(page.locator(FEATURED)).to_have_attribute("aria-label", featured)
    assert _rows(page) == rows


def describe_the_upcoming_queue():
    def it_starts_with_the_first_item_featured_and_the_rest_listed_in_order(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "queue-start-e2e@example.com")
        _expect_queue(page, TITLES[0], TITLES[1:])
        expect(page.locator(FEATURED).locator("[data-upnext-poster]")).to_be_visible()
        expect(page.locator(FEATURED).locator("[data-upnext-flyer]")).to_be_visible()

    def it_moves_the_next_item_into_the_card_and_the_featured_one_to_the_bottom_every_six_seconds(
        page, live_server, login_via_code
    ):
        _open_home(page, live_server, login_via_code, "queue-auto-e2e@example.com")
        page.clock.run_for(5500)
        _expect_queue(page, TITLES[0], TITLES[1:])
        page.clock.run_for(1000)
        _expect_queue(page, TITLES[1], [TITLES[2], TITLES[3], TITLES[0]])
        page.clock.run_for(6000)
        _expect_queue(page, TITLES[2], [TITLES[3], TITLES[0], TITLES[1]])

    def it_slides_the_rows_up_while_it_moves(page, live_server, login_via_code):
        # Every Web Animation starts paused at its first frame, so the move's animations stay put to be read
        # (they run on real time, not Playwright's fake clock) until the spec finishes them itself.
        page.add_init_script(
            """(() => {
                const animate = Element.prototype.animate;
                Element.prototype.animate = function (...args) {
                    const animation = animate.apply(this, args);
                    animation.pause();
                    return animation;
                };
            })()"""
        )
        _open_home(page, live_server, login_via_code, "queue-slide-e2e@example.com")
        page.locator(f"{QUEUE} [data-queue-next]").click()
        frames = page.evaluate(
            """() => [...document.querySelectorAll('[data-home-upcoming] [data-queue-list] > [data-queue-row]:not([hidden])')]
                .map((row) => row.getAnimations().map((a) => a.effect.getKeyframes()[0]))"""
        )
        # The two rows that stayed start one row lower and slide up; the row that came back rises in from below.
        assert [len(row) for row in frames] == [1, 1, 1]
        stayed, came_back = frames[:2], frames[2]
        assert all(
            row[0]["transform"].startswith("translateY(") and row[0]["transform"] != "translateY(0px)" for row in stayed
        )
        assert float(stayed[0][0]["transform"][len("translateY(") : -len("px)")]) > 0
        assert float(came_back[0]["opacity"]) == 0
        # The row whose item was featured lifts away as a copy, which removes itself when its animation ends.
        expect(page.locator(f"{QUEUE} [data-queue-ghost]")).to_have_count(1)
        page.evaluate("() => document.getAnimations().forEach((a) => a.finish())")
        expect(page.locator(f"{QUEUE} [data-queue-ghost]")).to_have_count(0)
        _expect_queue(page, TITLES[1], [TITLES[2], TITLES[3], TITLES[0]])

    def it_holds_still_while_pointed_at(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "queue-hover-e2e@example.com")
        page.locator(f"{QUEUE} [data-queue-list]").hover()
        page.clock.run_for(20000)
        _expect_queue(page, TITLES[0], TITLES[1:])
        _rest(page)
        page.clock.run_for(6500)
        _expect_queue(page, TITLES[1], [TITLES[2], TITLES[3], TITLES[0]])

    def it_holds_still_once_paused(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "queue-pause-e2e@example.com")
        pause = page.locator(f"{QUEUE} [data-queue-pause]")
        pause.click()
        expect(pause).to_have_attribute("aria-label", "Play the upcoming list")
        _rest(page)
        page.clock.run_for(20000)
        _expect_queue(page, TITLES[0], TITLES[1:])

    def it_moves_both_ways_with_the_arrows(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "queue-arrows-e2e@example.com")
        page.locator(QUEUE).hover()
        page.locator(f"{QUEUE} [data-queue-next]").click()
        _expect_queue(page, TITLES[1], [TITLES[2], TITLES[3], TITLES[0]])
        page.locator(f"{QUEUE} [data-queue-prev]").click()
        _expect_queue(page, TITLES[0], TITLES[1:])
        page.locator(f"{QUEUE} [data-queue-prev]").click()
        _expect_queue(page, TITLES[3], [TITLES[0], TITLES[1], TITLES[2]])

    def it_never_moves_by_itself_under_reduced_motion(page, live_server, login_via_code):
        page.emulate_media(reduced_motion="reduce")
        _open_home(page, live_server, login_via_code, "queue-reduced-e2e@example.com")
        expect(page.locator(f"{QUEUE} [data-queue-pause]")).to_be_hidden()
        page.clock.run_for(30000)
        _expect_queue(page, TITLES[0], TITLES[1:])
        page.locator(QUEUE).hover()
        page.locator(f"{QUEUE} [data-queue-next]").click()
        _expect_queue(page, TITLES[1], [TITLES[2], TITLES[3], TITLES[0]])
        expect(page.locator(f"{QUEUE} [data-queue-ghost]")).to_have_count(0)

    def it_holds_while_a_link_in_it_has_focus_and_keeps_that_focus(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "queue-link-focus-e2e@example.com")
        flyer = page.locator(f"{FEATURED} [data-upnext-flyer]")
        flyer.evaluate("(el) => el.focus()")
        _rest_mouse(page)
        page.clock.run_for(20000)
        _expect_queue(page, TITLES[0], TITLES[1:])
        assert page.evaluate("() => document.activeElement.hasAttribute('data-upnext-flyer')")

    def it_keeps_moving_after_a_click_on_an_arrow(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "queue-arrow-click-e2e@example.com")
        page.locator(QUEUE).hover()
        page.locator(f"{QUEUE} [data-queue-next]").click()
        _expect_queue(page, TITLES[1], [TITLES[2], TITLES[3], TITLES[0]])
        _rest_mouse(page)
        assert page.evaluate("() => document.activeElement.hasAttribute('data-queue-next')")
        page.clock.run_for(6500)
        _expect_queue(page, TITLES[2], [TITLES[3], TITLES[0], TITLES[1]])

    def it_holds_for_keyboard_focus_in_the_list(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "queue-focus-e2e@example.com")
        for _ in range(150):
            page.keyboard.press("Tab")
            if page.evaluate("() => !!document.activeElement.closest('[data-home-upcoming] [data-queue-list]')"):
                break
        else:
            raise AssertionError("Tab never reached the queue's rows")
        page.clock.run_for(20000)
        _expect_queue(page, TITLES[0], TITLES[1:])

    def it_runs_one_clock_after_leaving_home_and_coming_back(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "queue-boost-e2e@example.com")
        page.locator(".pl-quicklink", has_text="Calendar").click()
        page.wait_for_url("**/calendar/**")
        page.locator(".hub-sidebar__link", has_text="Home").first.click()
        page.wait_for_url(f"{live_server.url}{reverse('hub_home')}")
        expect(page.locator(f"{QUEUE} [data-queue-next]")).to_have_count(1)
        _rest(page)
        _expect_queue(page, TITLES[0], TITLES[1:])
        page.clock.run_for(6500)
        _expect_queue(page, TITLES[1], [TITLES[2], TITLES[3], TITLES[0]])
        page.clock.run_for(4000)
        _expect_queue(page, TITLES[1], [TITLES[2], TITLES[3], TITLES[0]])


def describe_without_javascript():
    def it_shows_the_first_item_featured_and_the_rest_listed_in_date_order(page, browser, live_server, login_via_code):
        _seed()
        _sign_in(login_via_code, "queue-nojs-e2e@example.com")
        # Signed in on the default page; carry its session into a context that runs no scripts.
        context = browser.new_context(java_script_enabled=False)
        try:
            context.add_cookies(page.context.cookies())
            nojs = context.new_page()
            nojs.goto(f"{live_server.url}{reverse('hub_home')}")
            expect(nojs.locator(FEATURED)).to_have_attribute("aria-label", TITLES[0])
            expect(nojs.locator(FEATURED)).to_be_visible()
            assert _rows(nojs) == TITLES[1:]
        finally:
            context.close()
