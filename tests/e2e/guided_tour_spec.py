"""End-to-end: the member-welcome guided tour, now an auto-navigating lap.

Drives the real offer -> Driver.js segment player -> state-recording loop in a
browser. The tour narrates on hub home, then DRIVES itself to the next screen
(catalog, calendar, voting, spaces, a guild, a class, help) via boosted
navigation, re-hydrating on each page from the "?tour=&step=" param it pushes,
until Done records ``completed`` and strips the param. Two more scenarios pin
the contract: Esc mid-tour records ``dismissed``, and landing directly on
``?tour=member-welcome&step=N`` resumes mid-lap.

The Python suite cannot exercise ``pl_tour.js``; this is the real runtime guard.
Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from core.models import TourState
from tests.membership.factories import GuildFactory, MembershipPlanFactory


def _seed_member_world() -> None:
    """A plan (so login auto-creates the member), a guild, and a bookable class.

    The guild backs the tour's orientation stop and the future-dated bookable
    class backs its register stop, so the full multi-page lap has real
    navigation targets rather than dropping those steps.
    """
    from classes.factories import ClassOfferingFactory, ClassSessionFactory
    from classes.models import ClassOffering

    MembershipPlanFactory()
    GuildFactory(name="Fiber Arts Guild")
    offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, is_private=False)
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=7),
        ends_at=timezone.now() + timedelta(days=7, hours=2),
    )


def _open_home_with_offer(live_server, page, login_via_code, email: str):
    """Log in (welcome modal pre-dismissed by the fixture) and land on hub home."""
    _seed_member_world()
    login_via_code(email)
    page.goto(f"{live_server.url}{reverse('hub_home')}")
    offer = page.locator("[data-pl-tour-offer]")
    expect(offer).to_be_visible()
    return offer


def _drive_to_completion(page) -> None:
    """Click Next through every step, following each cross-page hop.

    Progress reads a global "N of M"; after a Next that is not the final Done we
    wait until that text changes (a same-page move) or the popover detaches (a
    boosted navigation), so the loop is resilient to the tour driving the browser.
    """
    for _ in range(40):
        # A hop is a full navigation; let the destination finish loading (and its
        # tour bootstrap run) before we look for the popover.
        page.wait_for_load_state("load")
        popover = page.locator(".driver-popover.pl-tour")
        popover.wait_for(state="visible", timeout=10000)
        progress = popover.locator(".driver-popover-progress-text").inner_text().strip()
        next_btn = popover.locator(".driver-popover-next-btn")
        label = next_btn.inner_text().strip()
        next_btn.click()
        if label == "Done":
            return
        page.wait_for_function(
            "(prev) => { const el = document.querySelector('.driver-popover-progress-text');"
            " return !el || el.textContent.trim() !== prev; }",
            arg=progress,
            timeout=10000,
        )
    raise AssertionError("guided tour never reached its Done step")


def describe_member_welcome_tour():
    def it_runs_the_offered_tour_across_pages_to_completion(live_server, page, login_via_code):
        email = "tour-complete@example.com"
        offer = _open_home_with_offer(live_server, page, login_via_code, email)

        # Accept the offer — the card goes away and the restyled popover appears
        # (Driver.js is injected lazily right here, never on the plain pageview).
        offer.get_by_text("Show me around").click()
        expect(offer).not_to_be_visible()
        popover = page.locator(".driver-popover.pl-tour")
        expect(popover).to_be_visible()
        expect(popover).to_contain_text("Welcome to the Member Portal")

        _drive_to_completion(page)

        # Completion recorded; the offer never returns and the ?tour= param is
        # stripped so a refresh does not silently restart the tour.
        user = _user_for(email)
        _wait_for_status(page, user, "member-welcome", TourState.Status.COMPLETED)
        assert "tour=" not in page.url
        page.goto(f"{live_server.url}{reverse('hub_home')}")
        expect(page.locator("[data-pl-tour-offer]")).not_to_be_visible()

    def it_resumes_mid_tour_from_the_url_param(live_server, page, login_via_code):
        email = "tour-resume@example.com"
        _seed_member_world()
        login_via_code(email)

        # Land directly on a later step; the tour re-hydrates there.
        page.goto(f"{live_server.url}{reverse('hub_home')}?tour=member-welcome&step=1")
        popover = page.locator(".driver-popover.pl-tour")
        expect(popover).to_be_visible()
        expect(popover).to_contain_text("Everything in One Place")  # step index 1

    def it_escapes_mid_tour_and_records_a_dismissal(live_server, page, login_via_code):
        email = "tour-escape@example.com"
        offer = _open_home_with_offer(live_server, page, login_via_code, email)

        offer.get_by_text("Show me around").click()
        popover = page.locator(".driver-popover.pl-tour")
        expect(popover).to_be_visible()

        # Esc must close the popover instantly — this only works because our
        # onDestroyStarted hook calls destroy() itself — and record `dismissed`.
        page.keyboard.press("Escape")
        expect(popover).not_to_be_visible()
        user = _user_for(email)
        _wait_for_status(page, user, "member-welcome", TourState.Status.DISMISSED)

    def it_steps_back_across_a_page_hop(live_server, page, login_via_code):
        # Back from the first step of the second segment (on the catalog) must NAVIGATE
        # home to the entry-page segment, not strand the user on the catalog with a
        # centered popover — the entry-page leader has no `navigate`, so hopTo must fall
        # back to its page_path.
        email = "tour-back@example.com"
        _seed_member_world()
        login_via_code(email)
        page.goto(f"{live_server.url}{reverse('classes:public_list')}?tour=member-welcome&step=3")
        popover = page.locator(".driver-popover.pl-tour")
        popover.wait_for(state="visible", timeout=10000)
        expect(popover).to_contain_text("Browse Classes")  # step index 3, on the catalog

        popover.locator(".driver-popover-prev-btn").click()
        page.wait_for_url("**/home/**", timeout=10000)
        popover.wait_for(state="visible", timeout=10000)
        expect(popover).to_contain_text("Your Get Started List")  # step index 2, back on home


def _start_mid_tour(live_server, page, login_via_code, email: str):
    """Land directly on step 1 — an element-anchored popover on hub home.

    Step 0 is a *centered* popover whose Driver.js placement is not what these
    tests exercise; starting on the sidebar-anchored step 1 (the same reliable
    entry the resume test uses) keeps the popover on-screen and clickable.
    """
    _seed_member_world()
    login_via_code(email)
    page.goto(f"{live_server.url}{reverse('hub_home')}?tour=member-welcome&step=1")
    popover = page.locator(".driver-popover.pl-tour")
    popover.wait_for(state="visible", timeout=10000)
    expect(popover).to_contain_text("Everything in One Place")  # step index 1
    return popover


def describe_pausing_a_tour():
    def it_pauses_and_resumes_on_the_same_page(live_server, page, login_via_code):
        email = "tour-pause@example.com"
        popover = _start_mid_tour(live_server, page, login_via_code, email)
        progress = popover.locator(".driver-popover-progress-text").inner_text().strip()

        # Pause lifts the spotlight without ending the tour: popover gone, pill shown.
        popover.locator(".pl-tour-pause-btn").click()
        expect(popover).not_to_be_visible()
        pill = page.locator("#pl-tour-resume")
        expect(pill).to_be_visible()

        # Resume brings the popover back at the SAME step (progress text matches).
        pill.locator("[data-tour-resume]").click()
        popover.wait_for(state="visible", timeout=10000)
        expect(popover).to_contain_text("Everything in One Place")
        assert popover.locator(".driver-popover-progress-text").inner_text().strip() == progress
        expect(page.locator("#pl-tour-resume")).not_to_be_visible()

    def it_keeps_the_pill_while_you_wander_to_another_page(live_server, page, login_via_code):
        email = "tour-wander@example.com"
        popover = _start_mid_tour(live_server, page, login_via_code, email)
        popover.locator(".pl-tour-pause-btn").click()
        expect(page.locator("#pl-tour-resume")).to_be_visible()

        # Wander to a normal member page that carries NO tour payload. The paused
        # run lives in sessionStorage, so the pill follows the presenter there —
        # and the page shows neither the offer nor a spotlight.
        page.goto(f"{live_server.url}{reverse('hub_community_calendar')}")
        expect(page.locator("#pl-tour-resume")).to_be_visible()
        expect(page.locator(".driver-popover.pl-tour")).not_to_be_visible()
        expect(page.locator("[data-pl-tour-offer]")).not_to_be_visible()

        # Resume drives back to the paused step's page and re-spotlights it.
        page.locator("#pl-tour-resume [data-tour-resume]").click()
        page.wait_for_url("**/home/**", timeout=10000)
        popover.wait_for(state="visible", timeout=10000)
        expect(popover).to_contain_text("Everything in One Place")

    def it_preserves_the_page_query_string_on_resume(live_server, page, login_via_code):
        # A step whose page depends on a query (?audience=..., ?tab=payments) must
        # resume onto the SAME view. Prove a benign query survives pause -> resume.
        email = "tour-query@example.com"
        _seed_member_world()
        login_via_code(email)
        page.goto(f"{live_server.url}{reverse('hub_home')}?tour=member-welcome&step=1&demo=keepme")
        popover = page.locator(".driver-popover.pl-tour")
        popover.wait_for(state="visible", timeout=10000)
        popover.locator(".pl-tour-pause-btn").click()
        pill = page.locator("#pl-tour-resume")
        expect(pill).to_be_visible()

        pill.locator("[data-tour-resume]").click()
        page.wait_for_url("**demo=keepme**", timeout=10000)
        popover.wait_for(state="visible", timeout=10000)
        assert "demo=keepme" in page.url

    def it_lets_an_explicit_tour_link_win_over_a_paused_run(live_server, page, login_via_code):
        # A stale paused run must not swallow a deliberate ?tour= start (e.g. the
        # presenter opens a different role tour). The explicit autostart wins.
        email = "tour-win@example.com"
        popover = _start_mid_tour(live_server, page, login_via_code, email)
        popover.locator(".pl-tour-pause-btn").click()
        expect(page.locator("#pl-tour-resume")).to_be_visible()

        page.goto(f"{live_server.url}{reverse('classes:public_list')}?tour=member-welcome&step=3")
        popover.wait_for(state="visible", timeout=10000)
        expect(popover).to_contain_text("Browse Classes")  # step index 3, on the catalog
        expect(page.locator("#pl-tour-resume")).not_to_be_visible()

    def it_ends_a_paused_tour_from_the_pill(live_server, page, login_via_code):
        email = "tour-pause-end@example.com"
        popover = _start_mid_tour(live_server, page, login_via_code, email)
        popover.locator(".pl-tour-pause-btn").click()
        pill = page.locator("#pl-tour-resume")
        expect(pill).to_be_visible()

        # The pill's end button ends the tour for good: pill gone, dismissal recorded.
        pill.locator("[data-tour-resume-end]").click()
        expect(pill).not_to_be_visible()
        user = _user_for(email)
        _wait_for_status(page, user, "member-welcome", TourState.Status.DISMISSED)


def _user_for(email: str):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.get(username=email)


def _wait_for_status(page, user, tour_key: str, expected: str) -> None:
    """Poll the DB — the state POST is fired from the browser and lands async.

    Patient by design: the POST returns 204 promptly against a real server, but
    under the machine-speed e2e (a heavy final page, the service worker, no human
    pause) the browser can deprioritize it for several seconds. A human finishing
    the tour never notices; the poll just needs to outlast the test-infra latency.
    """
    for _ in range(60):
        if TourState.objects.status_for(user, tour_key) == expected:
            return
        page.wait_for_timeout(500)
    raise AssertionError(
        f"TourState for {tour_key} never became {expected!r}; got {TourState.objects.status_for(user, tour_key)!r}"
    )
