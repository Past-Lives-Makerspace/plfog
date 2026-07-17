"""End-to-end: the public catalog's "When" timeframe filter, in a real browser.

The filter form swaps ``#cls-results`` over HTMX on every change; only a browser
proves the select actually narrows the grid in place, and that the empty state's
"Show all upcoming" escape widens the timeframe again. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlparse

from django.urls import reverse
from django.utils import timezone
from playwright.sync_api import expect

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering


def _published_offering(title: str, slug: str, days_out: int) -> ClassOffering:
    offering = ClassOfferingFactory(
        title=title,
        slug=slug,
        status=ClassOffering.Status.PUBLISHED,
        is_private=False,
        price_cents=0,
    )
    ClassSessionFactory(
        class_offering=offering,
        starts_at=timezone.now() + timedelta(days=days_out),
        ends_at=timezone.now() + timedelta(days=days_out, hours=2),
    )
    return offering


def describe_catalog_timeframe_filter():
    def it_narrows_the_grid_to_the_chosen_window(live_server, page, settings):
        # Serve the catalog on the public/book surface (what customers see).
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        _published_offering("Casting Next Week", "casting-next-week", days_out=10)
        _published_offering("Weaving Next Season", "weaving-next-season", days_out=120)

        # All upcoming: both classes show.
        page.goto(f"{live_server.url}{reverse('classes:public_list')}")
        results = page.locator("#cls-results")
        expect(results).to_contain_text("Casting Next Week")
        expect(results).to_contain_text("Weaving Next Season")

        # Next 30 days: the far-out class drops from the swapped-in grid.
        page.select_option('select[name="within"]', "30")
        expect(results).to_contain_text("Casting Next Week")
        expect(results).not_to_contain_text("Weaving Next Season")

        # Next 180 days: both are back.
        page.select_option('select[name="within"]', "180")
        expect(results).to_contain_text("Weaving Next Season")

    def it_offers_a_show_all_escape_when_the_window_is_empty(live_server, page, settings):
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        _published_offering("Weaving Next Season", "weaving-next-season", days_out=120)

        # Land directly on an empty 30-day window: the escape link renders.
        page.goto(f"{live_server.url}{reverse('classes:public_list')}?within=30")
        results = page.locator("#cls-results")
        expect(results).not_to_contain_text("Weaving Next Season")
        escape = results.get_by_role("link", name="Show all upcoming")
        expect(escape).to_be_visible()

        # Following it widens the timeframe and the class appears.
        escape.click()
        expect(results).to_contain_text("Weaving Next Season")
