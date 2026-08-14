"""End-to-end: the Spaces map opens enlarged on a phone.

On a narrow (phone) viewport the interactive floor map must not render squeezed
to the screen width — ``space_map.js`` opens it at ``MOBILE_BASE_SCALE`` so rooms
stay legible and there is slack to pan. On a desktop viewport it stays 1:1.
These drive the real browser to prove the starting scale on each width and that a
drag pans the stage without erroring. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from decimal import Decimal

from django.urls import reverse
from playwright.sync_api import expect

from tests.membership.factories import FloorplanFactory, MapHotspotFactory, SpaceFactory

PHONE = {"width": 393, "height": 852}
DESKTOP = {"width": 1280, "height": 900}

# The stage's live horizontal scale (matrix "a"). Guard the pre-Alpine "none"
# that DOMMatrix cannot parse, treating it as the identity 1.
STAGE_SCALE = "el => { const t = getComputedStyle(el).transform; return t === 'none' ? 1 : new DOMMatrix(t).a; }"


def _seed_published_floor(*, aspect_ratio: str = "2.40") -> None:
    """A published, wide floor with one space marked — enough to draw the map.

    The wide aspect ratio guarantees horizontal pan slack once the stage opens
    enlarged, so the drag test has somewhere to go.
    """
    floor = FloorplanFactory(name="Ground Floor", image="", aspect_ratio=Decimal(aspect_ratio))
    MapHotspotFactory(floorplan=floor, space=SpaceFactory(space_id="A9"))


def describe_spaces_map_mobile():
    def it_opens_enlarged_on_a_phone(live_server, page):
        _seed_published_floor()
        page.set_viewport_size(PHONE)
        page.goto(f"{live_server.url}{reverse('hub_spaces')}")

        stage = page.locator(".pl-map-stage").first
        expect(stage).to_be_visible()
        # Wait past the open transition until the enlarged baseline has settled.
        page.wait_for_function(
            "() => { const el = document.querySelector('.pl-map-stage');"
            " if (!el) return false; const t = getComputedStyle(el).transform;"
            " return t !== 'none' && new DOMMatrix(t).a >= 2; }"
        )
        assert stage.evaluate(STAGE_SCALE) >= 2

    def it_stays_one_to_one_on_a_desktop_viewport(live_server, page):
        _seed_published_floor()
        page.set_viewport_size(DESKTOP)
        page.goto(f"{live_server.url}{reverse('hub_spaces')}")

        stage = page.locator(".pl-map-stage").first
        expect(stage).to_be_visible()
        page.wait_for_function(
            "() => { const el = document.querySelector('.pl-map-stage');"
            " if (!el) return false; const t = getComputedStyle(el).transform;"
            " return t === 'none' || Math.abs(new DOMMatrix(t).a - 1) < 0.01; }"
        )
        assert stage.evaluate(STAGE_SCALE) == 1

    def it_pans_the_stage_on_a_drag_without_erroring(live_server, page):
        _seed_published_floor()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.set_viewport_size(PHONE)
        page.goto(f"{live_server.url}{reverse('hub_spaces')}")

        viewport = page.locator(".pl-map-viewport").first
        expect(viewport).to_be_visible()
        box = viewport.bounding_box()
        # Start clear of the top-left marker (x 10–30%, y 10–25% of the canvas) and drag left.
        start_x = box["x"] + box["width"] * 0.8
        start_y = box["y"] + box["height"] * 0.8
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(start_x - 120, start_y, steps=8)
        page.mouse.up()

        # The stage's translateX moved off zero, and nothing threw.
        page.wait_for_function(
            "() => { const el = document.querySelector('.pl-map-stage');"
            " if (!el) return false; const t = getComputedStyle(el).transform;"
            " return t !== 'none' && Math.abs(new DOMMatrix(t).e) > 1; }"
        )
        translate_x = page.locator(".pl-map-stage").first.evaluate(
            "el => new DOMMatrix(getComputedStyle(el).transform).e"
        )
        assert translate_x != 0
        assert errors == []
