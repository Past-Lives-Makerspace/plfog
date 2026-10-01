"""End-to-end: the class page hero grows to fit its content and never clips it (#563).

The hero's content sits at the bottom of the box. While the box had a fixed height, a long
title or subtitle that wrapped on a phone pushed the category row out of the top, where the
box's overflow cut it off. Only a real browser lays the text out, so this drives the page at a
phone width and at a desktop width: the category row and the byline stay inside the hero, the
photo still covers the whole box, nothing scrolls sideways, and a class with no subtitle keeps
the box at its usual size. Run with ``pytest -m e2e``.
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
# The hero's floor at each width: clamp(280px, 42vw, 520px) in cms-public.css.
FLOOR = {390: 280, 1366: 520}


def _boxes(page: Page) -> dict[str, Any]:
    return page.evaluate(
        """() => {
          const box = (s) => { const r = document.querySelector(s).getBoundingClientRect();
                               return {top: r.top, bottom: r.bottom, left: r.left, right: r.right}; };
          return {
            hero: box('.cp-detail__hero'), img: box('.cp-detail__hero-img'),
            row: box('.cp-detail__cat-row'), byline: box('.cp-detail__byline'),
            overflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
          };
        }"""
    )


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
            page.goto(f"{live_server.url}{reverse('classes:public_class_detail', kwargs={'slug': offering.slug})}")
            page.wait_for_load_state("networkidle")
            page.evaluate("document.fonts.ready.then(() => true)")
            boxes = _boxes(page)
            hero = boxes["hero"]
            assert boxes["row"]["top"] >= hero["top"], offering.subtitle
            assert boxes["byline"]["bottom"] <= hero["bottom"], offering.subtitle
            assert boxes["img"] == hero, offering.subtitle
            assert boxes["overflow"] == 0, offering.subtitle

    @pytest.mark.parametrize("width", [390, 1366])
    def it_keeps_its_usual_size_without_a_subtitle(live_server, page: Page, serve_media, settings, width):
        offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED)
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        page.set_viewport_size({"width": width, "height": 900})
        page.goto(f"{live_server.url}{reverse('classes:public_class_detail', kwargs={'slug': offering.slug})}")
        page.wait_for_load_state("networkidle")
        box = page.locator(".cp-detail__hero").bounding_box()
        assert box is not None
        assert box["height"] == FLOOR[width]
