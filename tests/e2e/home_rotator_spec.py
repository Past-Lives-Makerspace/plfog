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
        expect(pause).to_have_attribute("aria-label", "Play the rotation")
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
