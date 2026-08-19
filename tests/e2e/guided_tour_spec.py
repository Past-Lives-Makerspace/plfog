"""End-to-end: the member-welcome guided tour (Spec C).

Drives the real offer → Driver.js run → state-recording loop in a browser:
offer card on hub home, "Show me around" spotlights the page, Next through to
Done records ``completed``, and the offer never returns. The Esc scenario pins
the review-hardened JS contract that only a browser can prove — providing
``onDestroyStarted`` suppresses Driver's default teardown, so our hook must
call ``destroy()`` itself or Esc/✕/overlay stop closing the tour at all.
Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from django.urls import reverse
from playwright.sync_api import expect

from core.models import TourState
from tests.membership.factories import GuildFactory, MembershipPlanFactory


def _seed_member_world() -> None:
    """A plan (so login auto-creates the member) and a guild for the sidebar."""
    MembershipPlanFactory()
    GuildFactory(name="Fiber Arts Guild")


def _open_home_with_offer(live_server, page, login_via_code, email: str):
    """Log in (welcome modal pre-dismissed by the fixture) and land on hub home."""
    _seed_member_world()
    login_via_code(email)
    page.goto(f"{live_server.url}{reverse('hub_home')}")
    offer = page.locator("[data-pl-tour-offer]")
    expect(offer).to_be_visible()
    return offer


def describe_member_welcome_tour():
    def it_runs_the_offered_tour_to_completion(live_server, page, login_via_code):
        email = "tour-complete@example.com"
        offer = _open_home_with_offer(live_server, page, login_via_code, email)

        # Accept the offer — the card goes away and the restyled popover appears
        # (Driver.js is injected lazily right here, never on the plain pageview).
        offer.get_by_text("Show me around").click()
        expect(offer).not_to_be_visible()
        popover = page.locator(".driver-popover.pl-tour")
        expect(popover).to_be_visible()
        expect(popover).to_contain_text("Welcome to the Member Portal")

        # Next through every surviving step until the last one shows Done.
        # (Hard cap of 12 — surviving steps vary with page state, never exceed 7.)
        next_btn = popover.locator(".driver-popover-next-btn")
        for _ in range(12):
            if next_btn.inner_text().strip() == "Done":
                break
            next_btn.click()
        expect(next_btn).to_have_text("Done")
        next_btn.click()
        expect(popover).not_to_be_visible()

        # Completion recorded; the offer never returns.
        user = _user_for(email)
        _wait_for_status(page, user, "member-welcome", TourState.Status.COMPLETED)
        page.goto(f"{live_server.url}{reverse('hub_home')}")
        expect(page.locator("[data-pl-tour-offer]")).not_to_be_visible()

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


def _user_for(email: str):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.get(username=email)


def _wait_for_status(page, user, tour_key: str, expected: str) -> None:
    """Poll the DB briefly — the state POST is fired from the browser."""
    for _ in range(40):
        if TourState.objects.status_for(user, tour_key) == expected:
            return
        page.wait_for_timeout(250)
    raise AssertionError(
        f"TourState for {tour_key} never became {expected!r}; got {TourState.objects.status_for(user, tour_key)!r}"
    )
