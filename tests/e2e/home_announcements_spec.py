"""The announcements banner at the top of Home in a real browser (#889, static/js/pl-rotator.js).

With several live announcements it moves on every 8 seconds, holds once paused, and runs its own clock
beside the Upcoming carousel's. With one there is nothing to press. Close hides it in this browser until
the set changes: a new or edited announcement brings it back, and with storage blocked it simply shows.
Driven on Playwright's fake clock, so no spec waits real seconds. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from core.factories import AnnouncementFactory
from core.models import Announcement, TourState
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

BANNER = "[data-home-announcements]"
ACTIVE = f"{BANNER} .pl-rotator__slide.is-active"
FEATURED_ACTIVE = "[data-home-featured] .pl-rotator__slide.is-active"


def _sign_in(login_via_code, email: str) -> None:
    MembershipPlanFactory()
    login_via_code(email)
    user = get_user_model().objects.get(username=email)
    member = Member.objects.get(user=user)
    member.welcome_dismissed_at = timezone.now()
    member.save(update_fields=["welcome_dismissed_at"])
    TourState.objects.mark_completed(user, "member-welcome")


def _announcements(count: int) -> list[Announcement]:
    return [
        AnnouncementFactory(body=f"<p>Zorblax notice <strong>{n + 1}</strong></p>", position=n) for n in range(count)
    ]


def _open_home(page, live_server, login_via_code, email: str, *, count: int = 3) -> None:
    _announcements(count)
    _sign_in(login_via_code, email)
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.clock.install()
    page.goto(f"{live_server.url}{reverse('hub_home')}")
    page.mouse.move(0, 0)
    expect(page.locator(BANNER)).to_be_visible()


def _current(page) -> str:
    return page.locator(ACTIVE).get_attribute("aria-label")


def _expect_slide(page, label: str) -> None:
    expect(page.locator(ACTIVE)).to_have_attribute("aria-label", label)


def describe_the_banner_rotating():
    def it_moves_on_by_itself_every_eight_seconds(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "ann-auto-e2e@example.com")
        assert _current(page) == "1 of 3"
        page.clock.run_for(7500)
        assert _current(page) == "1 of 3"
        page.clock.run_for(1000)
        _expect_slide(page, "2 of 3")
        expect(page.locator(ACTIVE)).to_contain_text("Zorblax notice 2")
        page.clock.run_for(8000)
        _expect_slide(page, "3 of 3")
        page.clock.run_for(8000)
        _expect_slide(page, "1 of 3")

    def it_holds_still_once_paused_and_moves_on_after_play(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "ann-pause-e2e@example.com")
        pause = page.locator(f"{BANNER} [data-rotator-pause]")
        pause.click()
        expect(pause).to_have_attribute("aria-label", "Play the Spotlight messages")
        page.mouse.move(0, 0)
        page.evaluate("() => document.activeElement.blur()")
        page.clock.run_for(30000)
        assert _current(page) == "1 of 3"
        pause.click()
        page.mouse.move(0, 0)
        page.evaluate("() => document.activeElement.blur()")
        page.clock.run_for(8500)
        _expect_slide(page, "2 of 3")

    def it_moves_with_its_arrows_and_dots(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "ann-controls-e2e@example.com")
        page.locator(BANNER).hover()
        page.locator(f"{BANNER} [data-rotator-next]").click()
        _expect_slide(page, "2 of 3")
        page.locator(f"{BANNER} [data-rotator-prev]").click()
        page.locator(f"{BANNER} [data-rotator-prev]").click()
        _expect_slide(page, "3 of 3")
        page.locator(f"{BANNER} [data-rotator-dot]").nth(1).click()
        _expect_slide(page, "2 of 3")

    def it_keeps_its_height_from_slide_to_slide(page, live_server, login_via_code):
        AnnouncementFactory(body=f"<p>{'A long Zorblax notice that wraps. ' * 4}</p>", position=9)
        _open_home(page, live_server, login_via_code, "ann-height-e2e@example.com")
        heights = set()
        for _ in range(4):
            heights.add(page.locator(BANNER).bounding_box()["height"])
            page.locator(f"{BANNER} [data-rotator-next]").click()
        assert len(heights) == 1

    def it_never_moves_by_itself_under_reduced_motion(page, live_server, login_via_code):
        page.emulate_media(reduced_motion="reduce")
        _open_home(page, live_server, login_via_code, "ann-reduced-e2e@example.com")
        expect(page.locator(f"{BANNER} [data-rotator-pause]")).to_be_hidden()
        page.clock.run_for(40000)
        assert _current(page) == "1 of 3"

    def it_runs_its_own_clock_beside_the_upcoming_carousel(page, live_server, login_via_code):
        for n in range(2):
            offering = ClassOfferingFactory(
                title=f"Zorblax class {n}",
                status=ClassOffering.Status.PUBLISHED,
                registration_cutoff_hours=None,
                image="",
            )
            start = timezone.now() + timedelta(days=3 + n)
            ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        _open_home(page, live_server, login_via_code, "ann-two-e2e@example.com")
        expect(page.locator("[data-home-featured] [data-rotator-dot]")).to_have_count(2)
        page.locator(f"{BANNER} [data-rotator-pause]").click()
        page.mouse.move(0, 0)
        page.evaluate("() => document.activeElement.blur()")
        page.clock.run_for(6500)
        expect(page.locator(FEATURED_ACTIVE)).to_have_attribute("aria-label", "2 of 2")
        assert _current(page) == "1 of 3"


def describe_one_announcement():
    def it_shows_no_controls_and_never_moves(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "ann-one-e2e@example.com", count=1)
        expect(page.locator(f"{BANNER} [data-rotator-dot]")).to_have_count(0)
        expect(page.locator(f"{BANNER} [data-rotator-pause]")).to_have_count(0)
        expect(page.locator(f"{BANNER} [data-rotator-next]")).to_have_count(0)
        page.clock.run_for(30000)
        expect(page.locator(ACTIVE)).to_contain_text("Zorblax notice 1")


def describe_closing_the_banner():
    def it_stays_closed_until_a_new_announcement_arrives(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "ann-close-e2e@example.com")
        page.locator(f"{BANNER} [data-announcement-close]").click()
        expect(page.locator(BANNER)).to_have_count(0)
        page.reload()
        expect(page.locator(".pl-quicklinks")).to_be_visible()
        expect(page.locator(BANNER)).to_have_count(0)

        AnnouncementFactory(body="<p>Zorblax brand new</p>", position=0)
        page.reload()
        expect(page.locator(BANNER)).to_be_visible()
        expect(page.locator(BANNER)).to_contain_text("Zorblax brand new")

    def it_comes_back_when_an_announcement_is_edited(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "ann-edit-e2e@example.com", count=1)
        page.locator(f"{BANNER} [data-announcement-close]").click()
        page.reload()
        expect(page.locator(BANNER)).to_have_count(0)

        announcement = Announcement.objects.get()
        announcement.body = "<p>Zorblax edited</p>"
        announcement.save()
        page.reload()
        expect(page.locator(BANNER)).to_contain_text("Zorblax edited")

    def it_stays_closed_when_only_the_order_changes(page, live_server, login_via_code):
        _open_home(page, live_server, login_via_code, "ann-order-e2e@example.com", count=2)
        page.locator(f"{BANNER} [data-announcement-close]").click()
        first, second = Announcement.objects.in_order()
        Announcement.reorder([second.pk, first.pk])
        page.reload()
        expect(page.locator(".pl-quicklinks")).to_be_visible()
        expect(page.locator(BANNER)).to_have_count(0)

    def it_shows_and_closes_with_storage_blocked(page, live_server, login_via_code):
        page.add_init_script(
            "Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } });"
        )
        _open_home(page, live_server, login_via_code, "ann-blocked-e2e@example.com")
        page.clock.run_for(8500)
        _expect_slide(page, "2 of 3")
        page.locator(f"{BANNER} [data-announcement-close]").click()
        expect(page.locator(BANNER)).to_have_count(0)
        page.reload()
        expect(page.locator(BANNER)).to_be_visible()
