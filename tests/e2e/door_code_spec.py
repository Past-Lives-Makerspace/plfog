"""The Door Code on Home in a real browser (#813, in the utility row since #1007).

Home renders the code twice: a one line chip at the right of the utility row above the greeting from
1200px, and a card under the quick links below that; CSS shows exactly one. Either starts masked on
every load: tapping it reveals the code, tapping again hides it, and the keyboard does the same. An
admin sets a code through Change at desktop and phone width. A revealed 20 character code pushes
nothing sideways at any width. From 1200px the chip sits on the row's right, above the greeting, and
Show Me Around stands on the row's left level with it, the greeting's description on one line under
the row; once the tour is taken there is no Show Me Around and the chip stays level with What's New.
The repo is public, so every code is an obviously fake value. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

import re
from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from core.models import SiteConfiguration, TourState
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

LONG_CODE = "1234567890#ABCDEFGH#"
CHIP = '[data-door-code-box="chip"]'
CARD = '[data-door-code-box="card"]'
ROW = "[data-home-utility]"
GREETING = "[data-home-greeting]"


def _viewport(width: int) -> dict[str, int]:
    return {"width": width, "height": 900}


def _sign_in(login_via_code, email: str, *, fog_role: str = Member.FogRole.MEMBER, tour_done: bool = False) -> None:
    MembershipPlanFactory()
    login_via_code(email)
    user = get_user_model().objects.get(username=email)
    member = Member.objects.get(user=user)
    member.fog_role = fog_role
    member.welcome_dismissed_at = timezone.now()
    # Tours switched off, so no Take the tour banner (#824); with the tour not yet taken, Home shows
    # Show Me Around. tour_done records the tour as taken, which hides Show Me Around.
    member.guided_tours_enabled = False
    member.save(update_fields=["fog_role", "welcome_dismissed_at", "guided_tours_enabled"])
    member.sync_user_permissions()
    if tour_done:
        TourState.objects.mark_completed(user, "member-welcome")


def _set_code(code: str) -> None:
    config = SiteConfiguration.load()
    config.door_code = code
    config.door_code_changed_at = datetime(2026, 10, 3, 18, 0, tzinfo=dt_timezone.utc)
    config.save()


def _home(page, live_server, width: int) -> None:
    page.set_viewport_size(_viewport(width))
    page.goto(f"{live_server.url}{reverse('hub_home')}")


def _shown_box(width: int) -> str:
    return CHIP if width >= 1200 else CARD


def _overflow(page) -> int:
    return page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")


def describe_which_box_shows():
    def it_shows_the_chip_and_not_the_card_at_1440(page, live_server, login_via_code):
        _set_code("4242#")
        _sign_in(login_via_code, "door-1440-e2e@example.com")
        _home(page, live_server, 1440)

        expect(page.locator(CHIP)).to_be_visible()
        expect(page.locator(CARD)).to_be_hidden()
        expect(page.locator(ROW).get_by_role("link", name="Show Me Around")).to_be_visible()

    @pytest.mark.parametrize("width", [390, 1199])
    def it_shows_the_card_and_not_the_chip_below_1200(page, live_server, login_via_code, width):
        _set_code("4242#")
        _sign_in(login_via_code, f"door-{width}-e2e@example.com")
        _home(page, live_server, width)

        expect(page.locator(CARD)).to_be_visible()
        expect(page.locator(CHIP)).to_be_hidden()
        expect(page.locator(ROW).get_by_role("link", name="Show Me Around")).to_be_visible()


@pytest.mark.parametrize("width", [390, 1440])
def describe_tap_to_reveal():
    def it_hides_the_code_until_tapped_and_hides_it_again(page, live_server, login_via_code, width):
        _set_code("4242#")
        _sign_in(login_via_code, f"door-tap-{width}-e2e@example.com")
        _home(page, live_server, width)
        box = page.locator(_shown_box(width))
        toggle = box.locator("[data-door-code-toggle]")

        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(box.locator("[data-door-code]")).to_be_hidden()
        expect(page.get_by_text("4242#")).to_have_count(2)
        for code in page.get_by_text("4242#").all():
            expect(code).to_be_hidden()
        expect(toggle).to_contain_text("Tap to reveal")
        expect(toggle).not_to_have_accessible_name(re.compile("4242"))

        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(box.locator("[data-door-code]")).to_have_text("4242#")
        expect(box.locator("[data-door-code]")).to_be_visible()
        expect(toggle).to_contain_text("Tap to hide")
        # The card says when the code changed; the one line chip leaves that out.
        if width < 1200:
            expect(box.locator("[data-door-code-changed]")).to_have_text("Changed Oct 3, 2026")
        else:
            expect(box.locator("[data-door-code-changed]")).to_have_count(0)
        expect(box.locator("[data-door-code-edit]")).to_have_count(0)

        toggle.click()
        expect(box.locator("[data-door-code]")).to_be_hidden()
        expect(toggle).to_have_attribute("aria-expanded", "false")

    def it_opens_and_closes_from_the_keyboard(page, live_server, login_via_code, width):
        _set_code("4242#")
        _sign_in(login_via_code, f"door-keys-{width}-e2e@example.com")
        _home(page, live_server, width)
        box = page.locator(_shown_box(width))
        toggle = box.locator("[data-door-code-toggle]")

        toggle.focus()
        page.keyboard.press("Enter")
        expect(box.locator("[data-door-code]")).to_be_visible()
        page.keyboard.press("Space")
        expect(box.locator("[data-door-code]")).to_be_hidden()
        expect(toggle).to_be_focused()

    def it_hides_the_code_again_on_the_next_load(page, live_server, login_via_code, width):
        _set_code("4242#")
        _sign_in(login_via_code, f"door-reload-{width}-e2e@example.com")
        _home(page, live_server, width)
        box = page.locator(_shown_box(width))
        box.locator("[data-door-code-toggle]").click()
        expect(box.locator("[data-door-code]")).to_be_visible()

        page.reload()

        expect(box.locator("[data-door-code]")).to_be_hidden()
        expect(box.locator("[data-door-code-toggle]")).to_have_attribute("aria-expanded", "false")

    def it_lets_an_admin_set_the_code_through_change(page, live_server, login_via_code, width):
        _sign_in(login_via_code, f"door-admin-{width}-e2e@example.com", fog_role=Member.FogRole.ADMIN)
        _home(page, live_server, width)
        box = page.locator(_shown_box(width))
        expect(box.locator("[data-door-code-empty]")).to_have_text("No code set yet.")

        box.locator("[data-door-code-edit]").click()
        form = page.locator("[data-door-code-form]")
        expect(form).to_be_visible()
        form.get_by_label("Door code").fill("  4242#  ")
        form.get_by_role("button", name="Save").click()

        expect(page.locator(".plt-toast", has_text="Door code saved.")).to_be_visible()
        expect(box.locator("[data-door-code]")).to_be_hidden()
        box.locator("[data-door-code-toggle]").click()
        expect(box.locator("[data-door-code]")).to_have_text("4242#")
        if width < 1200:
            expect(box.locator("[data-door-code-changed]")).to_contain_text("Changed")
        expect(box.locator("[data-door-code-edit]")).to_have_text("Change")
        assert SiteConfiguration.load().door_code == "4242#"


@pytest.mark.parametrize("width", [390, 1199, 1280, 1440, 1920])
def it_keeps_a_revealed_twenty_character_code_from_pushing_the_page_sideways(page, live_server, login_via_code, width):
    _set_code(LONG_CODE)
    _sign_in(login_via_code, f"door-wide-{width}-e2e@example.com", fog_role=Member.FogRole.ADMIN)
    _home(page, live_server, width)
    assert _overflow(page) <= 0
    box = page.locator(_shown_box(width))

    box.locator("[data-door-code-toggle]").click()

    code = box.locator("[data-door-code]")
    expect(code).to_have_text(LONG_CODE)
    assert _overflow(page) <= 0
    box_rect = box.bounding_box()
    code_rect = code.bounding_box()
    assert box_rect is not None
    assert code_rect is not None
    assert code_rect["x"] + code_rect["width"] <= box_rect["x"] + box_rect["width"] + 0.5


def it_puts_the_chip_at_the_rows_right_above_the_greeting_at_1280(page, live_server, login_via_code):
    _set_code(LONG_CODE)
    _sign_in(login_via_code, "door-row-1280-e2e@example.com", fog_role=Member.FogRole.ADMIN)
    _home(page, live_server, 1280)

    row = page.locator(ROW).bounding_box()
    button = page.locator("[data-whats-new-button]").bounding_box()
    title = page.locator(f"{GREETING} .hub-page-title").bounding_box()
    chip = page.locator(CHIP).bounding_box()
    assert row is not None
    assert button is not None
    assert title is not None
    assert chip is not None
    # At the row's right edge, on What's New's line, and the whole row above the greeting.
    assert abs((chip["x"] + chip["width"]) - (row["x"] + row["width"])) <= 1
    assert abs((chip["y"] + chip["height"] / 2) - (button["y"] + button["height"] / 2)) <= 1
    assert chip["y"] + chip["height"] <= title["y"]


@pytest.mark.parametrize("width", [1200, 1366, 1440, 1920])
def it_lines_show_me_around_up_with_the_chip_in_the_row(page, live_server, login_via_code, width):
    _set_code("4242#")
    _sign_in(login_via_code, f"door-align-{width}-e2e@example.com", fog_role=Member.FogRole.ADMIN)
    _home(page, live_server, width)

    row = page.locator(ROW).bounding_box()
    button = page.locator("[data-whats-new-button]").bounding_box()
    tour = page.locator(ROW).get_by_role("link", name="Show Me Around").bounding_box()
    chip = page.locator(CHIP).bounding_box()
    title = page.locator(f"{GREETING} .hub-page-title").bounding_box()
    description = page.locator(f"{GREETING} p.hub-text-muted").first.bounding_box()
    assert row is not None
    assert button is not None
    assert tour is not None
    assert chip is not None
    assert title is not None
    assert description is not None
    # One row: What's New, then Show Me Around as tall as it, the chip level with both on the right;
    # the greeting under the row with its description on one line.
    assert abs(tour["y"] - button["y"]) <= 0.5
    assert abs(tour["height"] - button["height"]) <= 0.5
    assert tour["x"] >= button["x"] + button["width"]
    assert abs((chip["y"] + chip["height"] / 2) - (tour["y"] + tour["height"] / 2)) <= 1
    assert chip["x"] >= tour["x"] + tour["width"]
    assert row["y"] + row["height"] <= title["y"]
    assert description["height"] < 30


@pytest.mark.parametrize("width", [1200, 1440, 1920])
def it_keeps_the_chip_level_with_whats_new_once_the_tour_is_taken(page, live_server, login_via_code, width):
    _set_code("4242#")
    _sign_in(login_via_code, f"door-taken-{width}-e2e@example.com", fog_role=Member.FogRole.ADMIN, tour_done=True)
    _home(page, live_server, width)

    expect(page.locator(ROW).get_by_role("link", name="Show Me Around")).to_have_count(0)
    row = page.locator(ROW).bounding_box()
    button = page.locator("[data-whats-new-button]").bounding_box()
    chip = page.locator(CHIP).bounding_box()
    title = page.locator(f"{GREETING} .hub-page-title").bounding_box()
    description = page.locator(f"{GREETING} p.hub-text-muted").first.bounding_box()
    assert row is not None
    assert button is not None
    assert chip is not None
    assert title is not None
    assert description is not None
    # Without Show Me Around the row is What's New and the chip, still one line, still above the greeting.
    assert abs((chip["y"] + chip["height"] / 2) - (button["y"] + button["height"] / 2)) <= 1
    assert abs(row["height"] - chip["height"]) <= 1
    assert row["y"] + row["height"] <= title["y"]
    assert description["height"] < 30
