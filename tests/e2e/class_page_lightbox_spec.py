"""End-to-end: the gallery lightbox paints over the whole class page, the hero title included.

The gallery sits in the booking rail, and on a laptop that rail is sticky. A sticky box is a
stacking context, so a lightbox rendered inside it, fixed and z-indexed as high as you like,
can never paint above anything the page stacks later, and the hero's content block does
(``.cp-detail__hero-content`` is positioned with its own z-index). The result was the class
title showing through the open lightbox, across the photo. The lightbox is now teleported to
``<body>`` (Alpine ``x-teleport``), so it stacks against the page and not the rail. Only a
browser stacks, so this clicks a gallery shot at a laptop width and asks what is painted at the
title's centre. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.urls import reverse
from playwright.sync_api import Page, expect

from classes.factories import ClassImageFactory, ClassOfferingFactory
from classes.models import ClassOffering

LIGHTBOX = ".cls-lightbox"


def _seed() -> ClassOffering:
    offering = ClassOfferingFactory(status=ClassOffering.Status.PUBLISHED, gallery=0)
    ClassImageFactory(class_offering=offering, sort_order=1)
    ClassImageFactory(class_offering=offering, sort_order=2)
    return offering


def _open(page: Page, live_server, offering: ClassOffering) -> None:
    page.goto(f"{live_server.url}{reverse('classes:public_class_detail', kwargs={'slug': offering.slug})}")
    page.wait_for_load_state("networkidle")


def _painted_at_title_centre(page: Page) -> str:
    """The class of the lightbox ancestor, if any, of whatever is painted at the hero title's centre."""
    return page.evaluate(
        """() => {
          const r = document.querySelector('.cp-detail__title').getBoundingClientRect();
          const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
          const box = hit && hit.closest('.cls-lightbox');
          return box ? box.className : (hit ? hit.className : '');
        }"""
    )


def describe_the_gallery_lightbox():
    def it_covers_the_hero_title_at_a_laptop_width(live_server, page: Page, serve_media, settings):
        offering = _seed()
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        page.set_viewport_size({"width": 1366, "height": 800})
        _open(page, live_server, offering)
        page.locator(".cls-gallery__main").click()
        expect(page.locator(LIGHTBOX)).to_be_visible()
        assert page.locator(f"{LIGHTBOX} .cls-lightbox__img").is_visible()
        # The lightbox, and nothing of the hero, is what the pointer would land on over the title.
        assert _painted_at_title_centre(page) == "cls-lightbox"

    def it_still_closes_on_escape_and_steps_with_the_arrows(live_server, page: Page, serve_media, settings):
        offering = _seed()
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        page.set_viewport_size({"width": 1366, "height": 800})
        _open(page, live_server, offering)
        page.locator(".cls-gallery__main").click()
        expect(page.locator(LIGHTBOX)).to_be_visible()
        expect(page.locator(f"{LIGHTBOX} .cls-lightbox__counter")).to_have_text("1 / 2")
        page.keyboard.press("ArrowRight")
        expect(page.locator(f"{LIGHTBOX} .cls-lightbox__counter")).to_have_text("2 / 2")
        page.keyboard.press("Escape")
        expect(page.locator(LIGHTBOX)).to_be_hidden()
        assert page.evaluate("document.body.style.overflow") == ""
