"""The Door Code tile on Home in a real browser (#813).

The tile sits in the quick links and hides the code on every load: tapping it reveals the code,
Hide (or tapping the tile again) hides it. An admin with no code set opens the tile, saves one
through Set Code at phone width and lands back on Home with the toast and the tile hidden again.
Closed, the tile is as tall as the links on its row; a 20 character code, revealed, pushes
nothing sideways and leaves those links their height, at 390px and 1280px. The repo is public, so
every code is an obviously fake value. Run with ``pytest -m e2e`` on PostgreSQL.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from core.models import SiteConfiguration
from membership.models import Member
from tests.membership.factories import MembershipPlanFactory

PHONE = {"width": 390, "height": 844}
DESKTOP = {"width": 1280, "height": 900}
TILE = "[data-door-code-tile]"
TOGGLE = f"{TILE} [data-door-code-reveal]"
PANEL = f"{TILE} [data-door-code-panel]"
LONG_CODE = "1234567890#ABCDEFGH#"


def _sign_in(login_via_code, email: str, *, fog_role: str = Member.FogRole.MEMBER) -> None:
    MembershipPlanFactory()
    login_via_code(email)
    user = get_user_model().objects.get(username=email)
    member = Member.objects.get(user=user)
    member.fog_role = fog_role
    member.welcome_dismissed_at = timezone.now()
    member.save(update_fields=["fog_role", "welcome_dismissed_at"])
    member.sync_user_permissions()


def _set_code(code: str) -> None:
    config = SiteConfiguration.load()
    config.door_code = code
    config.door_code_changed_at = datetime(2026, 10, 3, 18, 0, tzinfo=dt_timezone.utc)
    config.save()


def _home(page, live_server) -> None:
    page.goto(f"{live_server.url}{reverse('hub_home')}")


def _tile_row(page) -> tuple[dict, list[dict]]:
    """The door code tile's box and the boxes of the quick links on its row (same top), in order."""
    return page.evaluate(
        """() => {
            const box = (el) => {
                const r = el.getBoundingClientRect();
                return { x: r.x, y: r.y, width: r.width, height: r.height };
            };
            const tile = document.querySelector("[data-door-code-tile]");
            const top = tile.getBoundingClientRect().top;
            const row = [...document.querySelectorAll(".pl-quicklinks > .pl-quicklink")]
                .filter((el) => el !== tile && Math.abs(el.getBoundingClientRect().top - top) < 1);
            return [box(tile), row.map(box)];
        }"""
    )


def _row_heights(page) -> list[list[float]]:
    """The heights of the quick links, grouped by row (same top)."""
    return page.evaluate(
        """() => {
            const rows = new Map();
            for (const el of document.querySelectorAll(".pl-quicklinks > .pl-quicklink")) {
                const r = el.getBoundingClientRect();
                const key = Math.round(r.top);
                rows.set(key, [...(rows.get(key) || []), r.height]);
            }
            return [...rows.values()];
        }"""
    )


def _overflow(page) -> int:
    return page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")


def describe_the_door_code_tile():
    def it_hides_the_code_until_tapped_and_hides_it_again(page, live_server, login_via_code):
        _set_code("4242#")
        _sign_in(login_via_code, "door-member-e2e@example.com")
        page.set_viewport_size(PHONE)
        _home(page, live_server)

        toggle = page.locator(TOGGLE)
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(page.locator(PANEL)).to_be_hidden()
        expect(page.get_by_text("4242#")).to_be_hidden()
        expect(toggle).to_have_accessible_name("Door Code")

        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(page.locator(f"{PANEL} [data-door-code]")).to_have_text("4242#")
        expect(page.locator(f"{PANEL} [data-door-code-changed]")).to_have_text("Changed Oct 3, 2026")
        expect(page.locator(f"{PANEL} [data-door-code-edit]")).to_have_count(0)

        page.locator(f"{PANEL} [data-door-code-hide]").click()
        expect(page.locator(PANEL)).to_be_hidden()
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(toggle).to_be_focused()

        toggle.click()
        expect(page.locator(PANEL)).to_be_visible()
        toggle.click()
        expect(page.locator(PANEL)).to_be_hidden()

    def it_opens_and_closes_from_the_keyboard(page, live_server, login_via_code):
        _set_code("4242#")
        _sign_in(login_via_code, "door-keys-e2e@example.com")
        page.set_viewport_size(DESKTOP)
        _home(page, live_server)

        page.locator(TOGGLE).focus()
        page.keyboard.press("Enter")
        expect(page.locator(PANEL)).to_be_visible()
        page.keyboard.press("Space")
        expect(page.locator(PANEL)).to_be_hidden()

    def it_hides_the_code_again_on_the_next_load(page, live_server, login_via_code):
        _set_code("4242#")
        _sign_in(login_via_code, "door-reload-e2e@example.com")
        page.set_viewport_size(PHONE)
        _home(page, live_server)
        page.locator(TOGGLE).click()
        expect(page.locator(PANEL)).to_be_visible()

        page.reload()

        expect(page.locator(PANEL)).to_be_hidden()
        expect(page.locator(TOGGLE)).to_have_attribute("aria-expanded", "false")

    def it_lets_an_admin_set_the_code_from_the_revealed_tile(page, live_server, login_via_code):
        _sign_in(login_via_code, "door-admin-e2e@example.com", fog_role=Member.FogRole.ADMIN)
        page.set_viewport_size(PHONE)
        _home(page, live_server)

        page.locator(TOGGLE).click()
        expect(page.locator(f"{PANEL} [data-door-code-empty]")).to_have_text("No code set")
        page.locator(f"{PANEL} [data-door-code-edit]").click()
        form = page.locator("[data-door-code-form]")
        expect(form).to_be_visible()
        form.get_by_label("Door code").fill("  4242#  ")
        form.get_by_role("button", name="Save").click()

        expect(page.locator(".plt-toast", has_text="Door code saved.")).to_be_visible()
        expect(page.locator(PANEL)).to_be_hidden()
        page.locator(TOGGLE).click()
        expect(page.locator(f"{PANEL} [data-door-code]")).to_have_text("4242#")
        expect(page.locator(f"{PANEL} [data-door-code-changed]")).to_contain_text("Changed")
        expect(page.locator(f"{PANEL} [data-door-code-edit]")).to_have_text("Change")
        assert SiteConfiguration.load().door_code == "4242#"

    @pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["phone", "desktop"])
    def it_keeps_its_row_even_while_closed(page, live_server, login_via_code, viewport):
        _set_code(LONG_CODE)
        _sign_in(login_via_code, f"door-row-{viewport['width']}-e2e@example.com", fog_role=Member.FogRole.ADMIN)
        page.set_viewport_size(viewport)
        _home(page, live_server)
        expect(page.locator(TOGGLE)).to_be_visible()

        tile, neighbours = _tile_row(page)

        assert neighbours, "the door code tile has no neighbour in its row to measure against"
        for box in neighbours:
            assert abs(box["height"] - tile["height"]) <= 1
        # Home's stylesheet touches the whole grid, so every row stays even, not only the tile's.
        for heights in _row_heights(page):
            assert max(heights) - min(heights) <= 1, heights

    @pytest.mark.parametrize("viewport", [PHONE, DESKTOP], ids=["phone", "desktop"])
    def it_keeps_a_revealed_twenty_character_code_inside_the_tile(page, live_server, login_via_code, viewport):
        _set_code(LONG_CODE)
        _sign_in(login_via_code, f"door-wide-{viewport['width']}-e2e@example.com", fog_role=Member.FogRole.ADMIN)
        page.set_viewport_size(viewport)
        _home(page, live_server)
        expect(page.locator(TOGGLE)).to_be_visible()
        closed_tile, closed_neighbours = _tile_row(page)
        assert closed_neighbours, "the door code tile has no neighbour in its row to measure against"

        page.locator(TOGGLE).click()

        code = page.locator(f"{PANEL} [data-door-code]")
        expect(code).to_be_visible()
        expect(code).to_have_text(LONG_CODE)
        assert _overflow(page) <= 0
        tile, neighbours = _tile_row(page)
        code_box = code.bounding_box()
        assert code_box is not None
        assert code_box["x"] + code_box["width"] <= tile["x"] + tile["width"] + 0.5
        # Still one grid cell: as wide as the tiles beside it, and they never stretch to the open
        # tile's height (they settle at their own height while it is open).
        assert len(neighbours) == len(closed_neighbours)
        for before, after in zip(closed_neighbours, neighbours, strict=True):
            assert abs(after["width"] - tile["width"]) <= 1
            assert after["height"] <= before["height"] + 1
            assert after["height"] < tile["height"]
        assert abs(tile["width"] - closed_tile["width"]) <= 1
