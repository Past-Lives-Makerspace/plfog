"""Home's carousel of classes and events rotates in a real browser (#896, static/js/pl-rotator.js).

It shows one slide at a time and moves on every 6 seconds. Pointing at it or pausing it stops it,
the arrows and dots move it, and under prefers-reduced-motion nothing moves by itself. Driven on
Playwright's fake clock, so no spec waits real seconds. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from core.models import TourState
from membership.models import Member
from tests.membership.factories import CommunityEventFactory, MembershipPlanFactory

CAROUSEL = "[data-home-featured]"
ACTIVE = f"{CAROUSEL} .pl-rotator__slide.is-active"


def _seed() -> None:
    for n in range(3):
        offering = ClassOfferingFactory(
            title=f"Rotating class {n}",
            status=ClassOffering.Status.PUBLISHED,
            registration_cutoff_hours=None,
            image="",
        )
        start = timezone.now() + timedelta(days=3 + n)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        CommunityEventFactory(community=True, title=f"Rotating event {n}")


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
    page.mouse.move(0, 0)
    expect(page.locator(f"{CAROUSEL} [data-rotator-dot]")).to_have_count(6)


def _current(page) -> str:
    return page.locator(ACTIVE).get_attribute("aria-label")


def _expect_slide(page, label: str) -> None:
    expect(page.locator(ACTIVE)).to_have_attribute("aria-label", label)


def describe_the_home_carousel():
    def it_moves_on_by_itself_every_six_seconds(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "rotator-auto-e2e@example.com")
        assert _current(page) == "1 of 6"
        page.clock.run_for(5500)
        assert _current(page) == "1 of 6"
        page.clock.run_for(1000)
        _expect_slide(page, "2 of 6")
        page.clock.run_for(6000)
        _expect_slide(page, "3 of 6")

    def it_holds_still_while_pointed_at(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "rotator-hover-e2e@example.com")
        page.locator(CAROUSEL).hover()
        page.clock.run_for(20000)
        assert _current(page) == "1 of 6"
        page.mouse.move(0, 0)
        page.clock.run_for(6500)
        _expect_slide(page, "2 of 6")

    def it_holds_still_once_paused(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "rotator-pause-e2e@example.com")
        pause = page.locator(f"{CAROUSEL} [data-rotator-pause]")
        pause.click()
        expect(pause).to_have_attribute("aria-label", "Play the classes and events")
        page.mouse.move(0, 0)
        page.evaluate("() => document.activeElement.blur()")
        page.clock.run_for(20000)
        assert _current(page) == "1 of 6"

    def it_moves_with_the_arrows_and_the_dots(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "rotator-controls-e2e@example.com")
        page.locator(f"{CAROUSEL} [data-rotator-next]").click()
        _expect_slide(page, "2 of 6")
        page.locator(f"{CAROUSEL} [data-rotator-prev]").click()
        page.locator(f"{CAROUSEL} [data-rotator-prev]").click()
        _expect_slide(page, "6 of 6")
        page.locator(f"{CAROUSEL} [data-rotator-dot]").nth(2).click()
        _expect_slide(page, "3 of 6")
        expect(page.locator(f"{CAROUSEL} [data-rotator-dot]").nth(2)).to_have_attribute("aria-current", "true")

    def it_never_moves_by_itself_under_reduced_motion(page, live_server, login_via_code):
        page.emulate_media(reduced_motion="reduce")
        _open_home(page, live_server, login_via_code, "rotator-reduced-e2e@example.com")
        expect(page.locator(f"{CAROUSEL} [data-rotator-pause]")).to_be_hidden()
        page.clock.run_for(30000)
        assert _current(page) == "1 of 6"
        page.locator(f"{CAROUSEL} [data-rotator-next]").click()
        _expect_slide(page, "2 of 6")


def _touch(page, kind: str, x: int, y: int) -> None:
    """A synthetic touch event on the carousel (a desktop browser sends no real ones)."""
    page.evaluate(
        """([kind, x, y]) => {
            const el = document.querySelector('[data-home-featured]');
            const ev = new Event(kind, { bubbles: true });
            const point = [{ clientX: x, clientY: y }];
            if (kind === 'touchstart') ev.touches = point; else ev.changedTouches = point;
            el.dispatchEvent(ev);
        }""",
        [kind, x, y],
    )


def describe_the_home_carousel_on_touch_and_navigation():
    def it_runs_one_clock_after_leaving_home_and_coming_back(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "rotator-boost-e2e@example.com")
        page.locator(".pl-quicklink", has_text="Calendar").click()
        page.wait_for_url("**/calendar/**")
        page.locator(".hub-sidebar__link", has_text="Home").first.click()
        page.wait_for_url(f"{live_server.url}{reverse('hub_home')}")
        expect(page.locator(f"{CAROUSEL} [data-rotator-dot]")).to_have_count(6)
        page.mouse.move(0, 0)
        page.evaluate("() => document.activeElement && document.activeElement.blur()")
        assert _current(page) == "1 of 6"
        page.clock.run_for(6500)
        _expect_slide(page, "2 of 6")
        page.clock.run_for(4000)
        assert _current(page) == "2 of 6"

    def it_ignores_a_mostly_vertical_drag_and_follows_a_sideways_swipe(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "rotator-swipe-e2e@example.com")
        _touch(page, "touchstart", 200, 300)
        _touch(page, "touchend", 260, 100)
        assert _current(page) == "1 of 6"
        _touch(page, "touchstart", 300, 300)
        _touch(page, "touchend", 200, 310)
        _expect_slide(page, "2 of 6")
        _touch(page, "touchstart", 200, 300)
        _touch(page, "touchend", 300, 290)
        _expect_slide(page, "1 of 6")

    def it_is_not_held_by_a_finger_passing_over_it(page, live_server, login_via_code):
        # A tap on a phone sends a touch pointerenter and then a compatibility mouseenter, and
        # never the leave events, so neither may hold the rotation.
        _open_home(page, live_server, login_via_code, "rotator-finger-e2e@example.com")
        page.evaluate(
            """() => {
                const el = document.querySelector('[data-home-featured]');
                el.dispatchEvent(new PointerEvent('pointerenter', { pointerType: 'touch' }));
                el.dispatchEvent(new MouseEvent('mouseenter'));
            }"""
        )
        page.clock.run_for(6500)
        _expect_slide(page, "2 of 6")

    def it_resumes_after_a_click_on_an_arrow_but_holds_for_keyboard_focus(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "rotator-focus-e2e@example.com")
        page.locator(f"{CAROUSEL} [data-rotator-next]").click()
        _expect_slide(page, "2 of 6")
        page.mouse.move(0, 0)
        page.clock.run_for(6500)
        _expect_slide(page, "3 of 6")

        # Tab to the carousel's first control, as a keyboard user would.
        for _ in range(120):
            page.keyboard.press("Tab")
            if page.evaluate(
                "() => !!document.activeElement.closest('[data-home-featured] .pl-rotator__overlay, [data-home-featured] .pl-rotator__nav')"
            ):
                break
        else:
            raise AssertionError("Tab never reached the carousel's controls")
        page.clock.run_for(20000)
        assert _current(page) == "3 of 6"
