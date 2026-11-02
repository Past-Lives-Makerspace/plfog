"""Open <feature> lands on the control, and Show me rings it in place, in a real browser (#1051).

The changelog is patched for the live server (same process) with an entry for What's New itself
that names Home's What's New button as its target. From the /whats-new/ page the entry's button
reads "Open What's New" and lands on Home with ?show=, where the button is ringed under the tour
engine's popover (the entry's title and first bullet) until Esc or a click anywhere; on Home the
same entry reads "Show me", closes the modal and rings in place with no navigation. A target no
element carries shows nothing and throws nothing. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import ConsoleMessage, expect

from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

TITLE = "E2E open buttons say where they go"
BULLET = "Open What's New now lands on the button itself."
POPOVER = ".driver-popover.pl-tour--show"
BUTTON = '[data-guidance-target="home.whats-new-button"]'


def _entries() -> list[dict]:
    today = timezone.localdate().isoformat()
    return [
        {
            "date": today,
            "title": TITLE,
            "changes": [BULLET, "A second bullet the popover never shows."],
            "slug": "e2e-open-destination",
            "audiences": ["members"],
            "feature": "home.whats-new",
            "guidance": "link",
            "target": "home.whats-new-button",
        },
        {
            "date": today,
            "title": "E2E entry whose control nobody renders",
            "changes": ["Nothing on the page carries this key."],
            "slug": "e2e-missing-control",
            "audiences": ["members"],
            "feature": "home.door-code",
            "guidance": "link",
            "target": "home.nothing-here",
        },
    ]


def _sign_in(login_via_code, email: str) -> None:
    MembershipPlanFactory()
    login_via_code(email)
    member = Member.objects.get(user=get_user_model().objects.get(username=email))
    member.welcome_dismissed_at = timezone.now()
    member.guided_tours_enabled = False
    member.save(update_fields=["welcome_dismissed_at", "guided_tours_enabled"])


def _watch_errors(page) -> list[str]:
    """Every uncaught page error and console error from here on."""
    seen: list[str] = []

    def on_console(message: ConsoleMessage) -> None:
        if message.type == "error":
            seen.append(f"console.error: {message.text}")

    page.on("pageerror", lambda error: seen.append(f"pageerror: {error}"))
    page.on("console", on_console)
    return seen


def _open_from_the_whats_new_page(page, live_server) -> None:
    """Press Open What's New on the /whats-new/ page and land on Home with the highlight up."""
    page.goto(f"{live_server.url}{reverse('hub_whats_new')}")
    entry = page.locator('[data-whats-new-entry="e2e-open-destination"]')
    link = entry.locator("[data-whats-new-open]")
    expect(link).to_have_text("Open What's New")
    expect(link).to_have_attribute("href", "/home/?show=home.whats-new-button")
    link.click()
    expect(page.locator(POPOVER)).to_be_visible()


def describe_open_the_feature():
    def it_lands_on_the_control_ringed_under_the_entrys_title_and_first_bullet_until_esc(
        page, live_server, login_via_code, monkeypatch
    ):
        monkeypatch.setattr("plfog.version.CHANGELOG", _entries())
        _sign_in(login_via_code, "wn-show-open@example.com")
        errors = _watch_errors(page)
        page.set_viewport_size({"width": 1280, "height": 900})
        _open_from_the_whats_new_page(page, live_server)

        popover = page.locator(POPOVER)
        expect(popover.locator(".driver-popover-title")).to_have_text(TITLE)
        expect(popover.locator(".driver-popover-description")).to_have_text(BULLET)
        expect(popover.locator(".driver-popover-footer")).to_be_hidden()
        button = page.locator(BUTTON)
        expect(button).to_have_class(re.compile(r"\bdriver-active-element\b"))
        expect(button).to_be_in_viewport()
        # The popover sits just under the control it names, not where the layout was before it settled.
        expect(popover.locator(".driver-popover-arrow")).to_have_class(
            re.compile(r"\bdriver-popover-arrow-side-bottom\b")
        )
        box, pop = button.bounding_box(), popover.bounding_box()
        assert box["y"] + box["height"] <= pop["y"] <= box["y"] + box["height"] + 40, (box, pop)
        expect(page.locator("html")).to_have_class(re.compile(r"\bpl-guidance-showing\b"))
        # The param is stripped once read, so a reload does not ring the control again.
        assert page.url.endswith(reverse("hub_home")), page.url

        page.keyboard.press("Escape")
        expect(popover).to_have_count(0)
        expect(page.locator("html")).not_to_have_class(re.compile(r"\bpl-guidance-showing\b"))
        expect(button).not_to_have_class(re.compile(r"\bdriver-active-element\b"))
        assert errors == []

    def it_ends_on_a_click_anywhere_the_popover_included(page, live_server, login_via_code, monkeypatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", _entries())
        _sign_in(login_via_code, "wn-show-click@example.com")
        page.set_viewport_size({"width": 1280, "height": 900})
        _open_from_the_whats_new_page(page, live_server)

        page.locator(POPOVER).locator(".driver-popover-description").click()
        expect(page.locator(POPOVER)).to_have_count(0)
        expect(page.locator("html")).not_to_have_class(re.compile(r"\bpl-guidance-showing\b"))


def describe_show_me():
    def it_closes_the_modal_and_rings_in_place_with_no_navigation(page, live_server, login_via_code, monkeypatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", _entries())
        _sign_in(login_via_code, "wn-show-here@example.com")
        page.set_viewport_size({"width": 1280, "height": 900})
        page.goto(f"{live_server.url}{reverse('hub_home')}")
        page.locator("[data-whats-new-button]").click()
        body = page.locator("#whats-new-body")
        entry = body.locator('[data-whats-new-entry="e2e-open-destination"]')
        expect(entry).to_be_visible()
        show = entry.locator("[data-whats-new-show]")
        expect(show).to_have_text("Show me")
        expect(entry.locator("[data-whats-new-open]")).to_have_count(0)

        show.click()
        expect(body).to_be_hidden()
        popover = page.locator(POPOVER)
        expect(popover).to_be_visible()
        expect(popover.locator(".driver-popover-title")).to_have_text(TITLE)
        expect(page.locator(BUTTON)).to_have_class(re.compile(r"\bdriver-active-element\b"))
        assert page.url.endswith(reverse("hub_home")), page.url


def describe_a_control_the_page_lacks():
    def it_shows_nothing_and_throws_nothing(page, live_server, login_via_code, monkeypatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", _entries())
        _sign_in(login_via_code, "wn-show-missing@example.com")
        errors = _watch_errors(page)
        page.goto(f"{live_server.url}{reverse('hub_home')}?show=home.nothing-here")
        expect(page.locator("[data-whats-new-button]")).to_be_visible()
        # The param is read and stripped at once; nothing is ringed, now or after the engine's wait.
        assert page.url.endswith(reverse("hub_home")), page.url
        page.wait_for_timeout(1500)
        expect(page.locator(".driver-popover")).to_have_count(0)
        expect(page.locator("html")).not_to_have_class(re.compile(r"\bpl-guidance-showing\b"))
        assert errors == []
