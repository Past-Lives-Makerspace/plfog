"""A rotator's pause lasts until play or logging out (#890, static/js/pl-rotator.js), in a real browser.

Each rotator is paused on its own, kept in localStorage under pl-rotator-paused:<user pk>:<name>: across a boosted
page change and a reload, while the others keep moving. Play clears it, logging out clears every one, a key of
another user is dropped on load, and with storage blocked a pause lasts for the page. Driven on Playwright's fake
clock, so no spec waits real seconds. Run with ``pytest -m e2e`` on PostgreSQL.
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
from core.models import TourState
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

BANNER = "[data-home-announcements]"
BANNER_ACTIVE = f"{BANNER} .pl-rotator__slide.is-active"
BANNER_PAUSE = f"{BANNER} [data-rotator-pause]"
SPOTLIGHT = ".hub-sidebar__spotlight [data-spotlight-announcements]"
SPOTLIGHT_COUNT = f"{SPOTLIGHT} [data-rotator-count]"
SPOTLIGHT_PAUSE = f"{SPOTLIGHT} [data-rotator-pause]"
CAROUSEL_ACTIVE = "[data-home-featured] .pl-rotator__slide.is-active"
PAUSED = "Play the Spotlight messages"
RUNNING = "Pause the Spotlight messages"


def _sign_in(page, login_via_code, email: str) -> int:
    MembershipPlanFactory()
    login_via_code(email)
    user = get_user_model().objects.get(username=email)
    member = Member.objects.get(user=user)
    member.welcome_dismissed_at = timezone.now()
    member.save(update_fields=["welcome_dismissed_at"])
    TourState.objects.mark_completed(user, "member-welcome")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.evaluate("() => localStorage.removeItem('plSpotlightMinimized')")
    return user.pk


def _announcements(count: int = 3) -> None:
    for n in range(count):
        AnnouncementFactory(body=f"<p>Zorblax notice {n + 1}</p>", position=n)


def _two_classes() -> None:
    for n in range(2):
        offering = ClassOfferingFactory(
            title=f"Zorblax class {n}",
            status=ClassOffering.Status.PUBLISHED,
            registration_cutoff_hours=None,
            image="",
        )
        start = timezone.now() + timedelta(days=3 + n)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))


def _go(page, live_server, name: str) -> None:
    page.goto(f"{live_server.url}{reverse(name)}")
    _rest(page)


def _rest(page) -> None:
    """No mouse on a rotator and no focus in one, so only a pause can hold it."""
    page.mouse.move(1430, 990)
    page.evaluate("() => document.activeElement && document.activeElement.blur()")


def _keys(page) -> list[str]:
    return page.evaluate(
        "() => Object.keys(localStorage).filter((key) => key.indexOf('pl-rotator-paused:') === 0).sort()"
    )


def describe_a_lasting_pause():
    def it_keeps_the_home_banner_paused_across_a_page_change_and_a_reload(page, live_server, login_via_code):
        _announcements()
        user_pk = _sign_in(page, login_via_code, "pause-banner-e2e@example.com")
        page.clock.install()
        _go(page, live_server, "hub_home")
        page.locator(BANNER_PAUSE).click()
        expect(page.locator(BANNER_PAUSE)).to_have_attribute("aria-label", PAUSED)
        assert _keys(page) == [f"pl-rotator-paused:{user_pk}:home-banner"]

        # Away with a full load, back with the sidebar's boosted Home link.
        page.goto(f"{live_server.url}{reverse('hub_beta_feedback')}")
        page.locator(f".hub-sidebar a[href='{reverse('hub_home')}']").first.click()
        expect(page.locator(BANNER)).to_be_visible()
        _rest(page)
        expect(page.locator(BANNER_PAUSE)).to_have_attribute("aria-label", PAUSED)
        page.clock.run_for(30000)
        expect(page.locator(BANNER_ACTIVE)).to_have_attribute("aria-label", "1 of 3")

        page.reload()
        _rest(page)
        expect(page.locator(BANNER_PAUSE)).to_have_attribute("aria-label", PAUSED)
        page.clock.run_for(30000)
        expect(page.locator(BANNER_ACTIVE)).to_have_attribute("aria-label", "1 of 3")

        # The arrows still move it while paused.
        page.locator(BANNER).hover()
        page.locator(f"{BANNER} [data-rotator-next]").click()
        expect(page.locator(BANNER_ACTIVE)).to_have_attribute("aria-label", "2 of 3")

    def it_keeps_the_spotlight_paused_on_every_page_without_pausing_homes_carousel(page, live_server, login_via_code):
        _announcements()
        _two_classes()
        user_pk = _sign_in(page, login_via_code, "pause-spotlight-e2e@example.com")
        page.clock.install()
        _go(page, live_server, "hub_beta_feedback")
        page.locator(SPOTLIGHT_PAUSE).click()
        assert _keys(page) == [f"pl-rotator-paused:{user_pk}:spotlight"]

        _go(page, live_server, "hub_home")
        expect(page.locator(SPOTLIGHT_PAUSE)).to_have_attribute("aria-label", PAUSED)
        expect(page.locator(BANNER_PAUSE)).to_have_attribute("aria-label", RUNNING)
        page.clock.run_for(8500)
        expect(page.locator(CAROUSEL_ACTIVE)).to_have_attribute("aria-label", "2 of 2")
        expect(page.locator(BANNER_ACTIVE)).to_have_attribute("aria-label", "2 of 3")
        expect(page.locator(SPOTLIGHT_COUNT)).to_have_text("1 of 3")

    def it_runs_again_after_play_and_forgets_it(page, live_server, login_via_code):
        _announcements()
        _sign_in(page, login_via_code, "pause-play-e2e@example.com")
        page.clock.install()
        _go(page, live_server, "hub_beta_feedback")
        page.locator(SPOTLIGHT_PAUSE).click()
        page.locator(SPOTLIGHT_PAUSE).click()
        expect(page.locator(SPOTLIGHT_PAUSE)).to_have_attribute("aria-label", RUNNING)
        assert _keys(page) == []

        page.reload()
        _rest(page)
        page.clock.run_for(8500)
        expect(page.locator(SPOTLIGHT_COUNT)).to_have_text("2 of 3")


def describe_forgetting_it():
    def it_starts_running_after_logging_out_and_back_in(page, live_server, login_via_code):
        _announcements()
        email = "pause-logout-e2e@example.com"
        _sign_in(page, login_via_code, email)
        _go(page, live_server, "hub_home")
        page.locator(BANNER_PAUSE).click()
        page.locator(SPOTLIGHT_PAUSE).click()
        assert len(_keys(page)) == 2

        page.goto(f"{live_server.url}{reverse('account_logout')}")
        page.locator("form[action='/accounts/logout/'] button[type='submit']").click()
        page.wait_for_url(lambda url: "/accounts/logout/" not in url)
        assert _keys(page) == []

        login_via_code(email)
        _go(page, live_server, "hub_home")
        expect(page.locator(BANNER_PAUSE)).to_have_attribute("aria-label", RUNNING)
        expect(page.locator(SPOTLIGHT_PAUSE)).to_have_attribute("aria-label", RUNNING)

    def it_drops_another_users_pause_on_load(page, live_server, login_via_code):
        _announcements()
        user_pk = _sign_in(page, login_via_code, "pause-shared-e2e@example.com")
        page.evaluate(
            "(pk) => { localStorage.setItem('pl-rotator-paused:' + (pk + 1000) + ':home-banner', '1');"
            " localStorage.setItem('pl-rotator-paused:' + pk + ':spotlight', '1') }",
            user_pk,
        )

        _go(page, live_server, "hub_home")

        expect(page.locator(BANNER_PAUSE)).to_have_attribute("aria-label", RUNNING)
        expect(page.locator(SPOTLIGHT_PAUSE)).to_have_attribute("aria-label", PAUSED)
        assert _keys(page) == [f"pl-rotator-paused:{user_pk}:spotlight"]

    def it_forgets_every_pause_on_a_signed_out_page(page, live_server):
        page.goto(f"{live_server.url}/accounts/login/")
        page.evaluate("() => localStorage.setItem('pl-rotator-paused:7:spotlight', '1')")

        page.reload()

        assert _keys(page) == []


def describe_with_storage_blocked():
    def it_pauses_for_the_page_and_runs_again_after_a_reload(page, live_server, login_via_code):
        _announcements()
        _sign_in(page, login_via_code, "pause-blocked-e2e@example.com")
        page.add_init_script(
            "Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } });"
        )
        page.clock.install()
        _go(page, live_server, "hub_home")
        page.locator(BANNER_PAUSE).click()
        expect(page.locator(BANNER_PAUSE)).to_have_attribute("aria-label", PAUSED)
        _rest(page)
        page.clock.run_for(30000)
        expect(page.locator(BANNER_ACTIVE)).to_have_attribute("aria-label", "1 of 3")

        page.reload()
        _rest(page)
        expect(page.locator(BANNER_PAUSE)).to_have_attribute("aria-label", RUNNING)
        page.clock.run_for(8500)
        expect(page.locator(BANNER_ACTIVE)).to_have_attribute("aria-label", "2 of 3")
