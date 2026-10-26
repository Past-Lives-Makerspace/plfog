"""A rotator's pause lasts until play or logging out (#890, static/js/pl-rotator.js), in a real browser.

Driven on Home's carousel, the one rotator left since #935. A pause is kept in localStorage under
pl-rotator-paused:<user pk>:<name>: across a boosted page change and a reload. Play clears it, logging out clears
every one, a key of another user is dropped on load, and with storage blocked a pause lasts for the page. Driven on
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
from tests.membership.factories import MembershipPlanFactory

CAROUSEL = "[data-home-featured]"
ACTIVE = f"{CAROUSEL} .pl-rotator__slide.is-active"
PAUSE = f"{CAROUSEL} [data-rotator-pause]"
PAUSED = "Play the classes and events"
RUNNING = "Pause the classes and events"
#: The carousel's name, the one rotator left since the Spotlight messages became a ticker (#935).
NAME = "home-upcoming"


def _sign_in(page, login_via_code, email: str) -> int:
    MembershipPlanFactory()
    login_via_code(email)
    user = get_user_model().objects.get(username=email)
    member = Member.objects.get(user=user)
    member.welcome_dismissed_at = timezone.now()
    member.save(update_fields=["welcome_dismissed_at"])
    TourState.objects.mark_completed(user, "member-welcome")
    page.set_viewport_size({"width": 1440, "height": 1000})
    return user.pk


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
    def it_keeps_the_carousel_paused_across_a_page_change_and_a_reload(page, live_server, login_via_code):
        _two_classes()
        user_pk = _sign_in(page, login_via_code, "pause-carousel-e2e@example.com")
        page.clock.install()
        _go(page, live_server, "hub_home")
        page.locator(PAUSE).click()
        expect(page.locator(PAUSE)).to_have_attribute("aria-label", PAUSED)
        assert _keys(page) == [f"pl-rotator-paused:{user_pk}:{NAME}"]

        # Away with a full load, back with the sidebar's boosted Home link.
        page.goto(f"{live_server.url}{reverse('hub_beta_feedback')}")
        page.locator(f".hub-sidebar a[href='{reverse('hub_home')}']").first.click()
        expect(page.locator(CAROUSEL)).to_be_visible()
        _rest(page)
        expect(page.locator(PAUSE)).to_have_attribute("aria-label", PAUSED)
        page.clock.run_for(30000)
        expect(page.locator(ACTIVE)).to_have_attribute("aria-label", "1 of 2")

        page.reload()
        _rest(page)
        expect(page.locator(PAUSE)).to_have_attribute("aria-label", PAUSED)
        page.clock.run_for(30000)
        expect(page.locator(ACTIVE)).to_have_attribute("aria-label", "1 of 2")

        # The arrows still move it while paused.
        page.locator(CAROUSEL).hover()
        page.locator(f"{CAROUSEL} [data-rotator-next]").click()
        expect(page.locator(ACTIVE)).to_have_attribute("aria-label", "2 of 2")

    def it_runs_again_after_play_and_forgets_it(page, live_server, login_via_code):
        _two_classes()
        _sign_in(page, login_via_code, "pause-play-e2e@example.com")
        page.clock.install()
        _go(page, live_server, "hub_home")
        page.locator(PAUSE).click()
        page.locator(PAUSE).click()
        expect(page.locator(PAUSE)).to_have_attribute("aria-label", RUNNING)
        assert _keys(page) == []

        page.reload()
        _rest(page)
        page.clock.run_for(6500)
        expect(page.locator(ACTIVE)).to_have_attribute("aria-label", "2 of 2")


def describe_forgetting_it():
    def it_starts_running_after_logging_out_and_back_in(page, live_server, login_via_code):
        _two_classes()
        email = "pause-logout-e2e@example.com"
        _sign_in(page, login_via_code, email)
        _go(page, live_server, "hub_home")
        page.locator(PAUSE).click()
        assert len(_keys(page)) == 1

        page.goto(f"{live_server.url}{reverse('account_logout')}")
        page.locator("form[action='/accounts/logout/'] button[type='submit']").click()
        page.wait_for_url(lambda url: "/accounts/logout/" not in url)
        assert _keys(page) == []

        login_via_code(email)
        _go(page, live_server, "hub_home")
        expect(page.locator(PAUSE)).to_have_attribute("aria-label", RUNNING)

    def it_drops_another_users_pause_on_load(page, live_server, login_via_code):
        _two_classes()
        user_pk = _sign_in(page, login_via_code, "pause-shared-e2e@example.com")
        page.evaluate(
            "([pk, name]) => { localStorage.setItem('pl-rotator-paused:' + (pk + 1000) + ':' + name, '1');"
            " localStorage.setItem('pl-rotator-paused:' + pk + ':' + name, '1') }",
            [user_pk, NAME],
        )

        _go(page, live_server, "hub_home")

        expect(page.locator(PAUSE)).to_have_attribute("aria-label", PAUSED)
        assert _keys(page) == [f"pl-rotator-paused:{user_pk}:{NAME}"]

    def it_forgets_every_pause_on_a_signed_out_page(page, live_server):
        page.goto(f"{live_server.url}/accounts/login/")
        page.evaluate("() => localStorage.setItem('pl-rotator-paused:7:home-upcoming', '1')")

        page.reload()

        assert _keys(page) == []


def describe_with_storage_blocked():
    def it_pauses_for_the_page_and_runs_again_after_a_reload(page, live_server, login_via_code):
        _two_classes()
        _sign_in(page, login_via_code, "pause-blocked-e2e@example.com")
        page.add_init_script(
            "Object.defineProperty(window, 'localStorage', { get() { throw new Error('blocked'); } });"
        )
        page.clock.install()
        _go(page, live_server, "hub_home")
        page.locator(PAUSE).click()
        expect(page.locator(PAUSE)).to_have_attribute("aria-label", PAUSED)
        _rest(page)
        page.clock.run_for(30000)
        expect(page.locator(ACTIVE)).to_have_attribute("aria-label", "1 of 2")

        page.reload()
        _rest(page)
        expect(page.locator(PAUSE)).to_have_attribute("aria-label", RUNNING)
        page.clock.run_for(6500)
        expect(page.locator(ACTIVE)).to_have_attribute("aria-label", "2 of 2")
