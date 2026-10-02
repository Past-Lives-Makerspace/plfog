"""End-to-end: the class page hero takes its photo's shape, shows all of it, and still fits its content.

The hero's content sits at the bottom of the box. While the box had a fixed height, a long
title or subtitle that wrapped on a phone pushed the category row out of the top, where the
box's overflow cut it off (#563). Now the box takes the photo's shape (``--cp-hero-ratio`` on
the frame, applied by the ``.cp-detail__hero--photo`` sizer), capped at 700px, with the photo
contain fitted so nothing of it is ever clipped, and the box still grows past the photo when
the content needs more. Only a real browser lays the text out and sizes the frame, so this
drives the page at a phone width and at a desktop width: the category row and the byline stay
inside the hero, the photo is never clipped, nothing scrolls sideways, and a class with no
subtitle sits at exactly the photo's height under the cap. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import pytest
from django.urls import reverse
from playwright.sync_api import Page

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering

WORDED_150 = (
    "Pick Your October Time, Saturday or Sunday, Morning or Evening, Bring a Friend and Leave with a "
    "Finished Leaf Dish of Your Own Design and Fresh Patina"
)
UNBROKEN_150 = ("PickYourOctoberTime" * 8)[:150]
LONG_TITLE = "Forge a Leaf Dish at the Anvil, Then Patina, Polish and Seal It for the Table in One Long Afternoon"
# The photo's height is capped here (max-height on the sizer in cms-public.css).
CAP = 700


def _boxes(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const box = (s) => { const r = document.querySelector(s).getBoundingClientRect();
                               return {top: r.top, bottom: r.bottom, left: r.left, right: r.right,
                                       width: r.width, height: r.height}; };
          const img = document.querySelector('.cp-detail__hero-img');
          return {
            hero: box('.cp-detail__hero'), img: box('.cp-detail__hero-img'),
            row: box('.cp-detail__cat-row'), byline: box('.cp-detail__byline'),
            natural: {width: img.naturalWidth, height: img.naturalHeight},
            fit: getComputedStyle(img).objectFit,
            overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          };
        }"""
    )


def _photo_height(boxes: dict[str, Any]) -> float:
    """The height the hero takes for its photo alone: the photo's shape at the hero's width, capped."""
    natural = boxes["natural"]
    return min(boxes["hero"]["width"] * natural["height"] / natural["width"], CAP)


def _assert_nothing_clipped(boxes: dict[str, Any]) -> None:
    """The img box fills the hero and the photo is contain fitted, so the whole photo is painted."""
    hero, img, natural = boxes["hero"], boxes["img"], boxes["natural"]
    assert natural["width"] > 0 and natural["height"] > 0
    assert boxes["fit"] == "contain"
    assert (img["top"], img["bottom"], img["left"], img["right"]) == (
        hero["top"],
        hero["bottom"],
        hero["left"],
        hero["right"],
    )


def _open(page: Page, live_server, offering: ClassOffering) -> None:
    page.goto(f"{live_server.url}{reverse('classes:public_class_detail', kwargs={'slug': offering.slug})}")
    page.wait_for_load_state("networkidle")
    page.evaluate("document.fonts.ready.then(() => true)")
    page.wait_for_function("() => document.querySelector('.cp-detail__hero-img').naturalWidth > 0")


def describe_the_class_page_hero():
    @pytest.mark.parametrize("width", [390, 1366])
    def it_keeps_a_long_title_and_subtitle_inside_the_box(live_server, page: Page, serve_media, settings, width):
        assert len(WORDED_150) == len(UNBROKEN_150) == 150
        cases = [
            ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, subtitle=WORDED_150),
            ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, subtitle=UNBROKEN_150),
            ClassOfferingFactory(
                status=ClassOffering.Status.PUBLISHED, title=LONG_TITLE, slug="long-title-hero", subtitle=WORDED_150
            ),
        ]
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        page.set_viewport_size({"width": width, "height": 900})
        for offering in cases:
            _open(page, live_server, offering)
            boxes = _boxes(page)
            hero = boxes["hero"]
            assert boxes["row"]["top"] >= hero["top"], offering.subtitle
            assert boxes["byline"]["bottom"] <= hero["bottom"], offering.subtitle
            # The content may need more than the photo's height; it never gets less.
            assert hero["height"] >= _photo_height(boxes) - 1, offering.subtitle
            _assert_nothing_clipped(boxes)
            assert boxes["overflow"] == 0, offering.subtitle

    @pytest.mark.parametrize("width", [390, 1366])
    def it_takes_the_photos_shape_without_a_subtitle(live_server, page: Page, serve_media, settings, width):
        # The factory's photo is square: a phone frame is as tall as it is wide and the photo
        # paints edge to edge; the 1240px desktop column would be 1240px tall, so the frame
        # hits the cap and the photo is pillarboxed over the backdrop.
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        page.set_viewport_size({"width": width, "height": 900})
        _open(page, live_server, offering)
        boxes = _boxes(page)
        hero, natural = boxes["hero"], boxes["natural"]
        assert natural == {"width": 4, "height": 4}
        assert hero["height"] == pytest.approx(_photo_height(boxes), abs=1)
        if width == 390:
            assert hero["width"] / hero["height"] == pytest.approx(natural["width"] / natural["height"], abs=0.01)
        else:
            assert hero["height"] == CAP
        _assert_nothing_clipped(boxes)
        assert boxes["overflow"] == 0
