"""The Take the tour banner on Home in a real browser (#824).

A member who has not taken or dismissed the member tour sees the banner at the top of Home, never
beside the floating offer card, and no Show me around in the header. The close button removes the
banner, records ``dismissed`` through ``hub_tour_state`` and the banner stays gone on reload, where
the header link is back. On a phone the Take the tour button stacks under the text. The tour itself
running from the banner is ``guided_tour_spec``. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from playwright.sync_api import expect

from core.models import TourState
from tests.membership.factories import MembershipPlanFactory

BANNER = "[data-tour-banner]"


def _home(page, live_server, login_via_code, email: str, width: int) -> None:
    MembershipPlanFactory()
    login_via_code(email)  # the fixture dismisses the welcome modal, so the tour is on offer
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{live_server.url}{reverse('hub_home')}")


def _header_link(page):
    return page.locator(".hub-page-header").get_by_role("link", name="Show me around")


def _status(email: str) -> str | None:
    return TourState.objects.status_for(get_user_model().objects.get(username=email), "member-welcome")


@pytest.mark.parametrize("width", [390, 1440])
def describe_the_banner():
    def it_shows_alone_with_no_floating_card_and_no_header_link(page, live_server, login_via_code, width):
        _home(page, live_server, login_via_code, f"banner-{width}-e2e@example.com", width)

        banner = page.locator(BANNER)
        expect(banner).to_be_visible()
        expect(banner.get_by_role("button", name="Take the tour")).to_be_visible()
        expect(page.locator(".pl-tour-offer")).to_have_count(0)
        expect(_header_link(page)).to_have_count(0)

    def it_dismisses_for_good_from_the_close_button(page, live_server, login_via_code, width):
        email = f"banner-close-{width}-e2e@example.com"
        _home(page, live_server, login_via_code, email, width)

        page.locator(BANNER).get_by_role("button", name="Dismiss the tour").click()
        expect(page.locator(BANNER)).to_have_count(0)
        # The POST lands async; as in guided_tour_spec, outlast the e2e lane's latency.
        for _ in range(60):
            if _status(email) == TourState.Status.DISMISSED:
                break
            page.wait_for_timeout(500)
        assert _status(email) == TourState.Status.DISMISSED

        page.reload()
        expect(page.locator(BANNER)).to_have_count(0)
        expect(_header_link(page)).to_be_visible()


def describe_on_a_phone():
    def it_stacks_the_button_under_the_text_without_sideways_scroll(page, live_server, login_via_code):
        _home(page, live_server, login_via_code, "banner-stack-e2e@example.com", 390)

        banner = page.locator(BANNER)
        text = banner.locator(".pl-tour-banner__body").bounding_box()
        button = banner.get_by_role("button", name="Take the tour").bounding_box()
        assert text is not None and button is not None
        assert button["y"] >= text["y"] + text["height"]
        assert page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth") <= 0
