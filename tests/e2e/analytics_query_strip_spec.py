"""End-to-end: search text never reaches Google Analytics, in a real browser.

``?q=`` carries free text on the admin changelist, the member directory, and the class
catalog, and that text is routinely a member's name or email address. The stripping runs
client-side (hx-boost builds URLs in the browser, so a server-rendered flag would only
protect the first page of a session), which means only a browser can prove it works.

These specs read ``window.dataLayer`` directly. The inline snippet queues its arguments
there synchronously, so the assertions hold whether or not gtag.js itself loads — which
matters, since the harness has no outbound network to Google. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from urllib.parse import urlparse

from django.urls import reverse

from core.models import SiteConfiguration

SEARCH_TERM = "member.private@example.com"


def _configure_ga() -> None:
    config = SiteConfiguration.load()
    config.google_analytics_measurement_id = "G-E2ESTRIP1"
    config.save()


def _page_locations(page) -> list[str]:
    """Every page_location queued into dataLayer, from config calls and page_view events."""
    return page.evaluate(
        """() => (window.dataLayer || [])
              .map(a => Array.from(a))
              .map(a => a.find(x => x && typeof x === 'object' && 'page_location' in x))
              .filter(Boolean)
              .map(o => o.page_location)"""
    )


def describe_analytics_query_stripping():
    def it_drops_the_search_term_from_the_catalog_page_location(live_server, page, settings):
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        _configure_ga()

        page.goto(f"{live_server.url}{reverse('classes:public_list')}?q={SEARCH_TERM}")

        locations = _page_locations(page)
        assert locations, "expected at least one page_location queued into dataLayer"
        for location in locations:
            assert SEARCH_TERM not in location
            assert "?" not in location

    def it_keeps_utm_campaign_parameters_intact(live_server, page, settings):
        settings.PUBLIC_HOSTS = [urlparse(live_server.url).hostname]
        _configure_ga()

        page.goto(f"{live_server.url}{reverse('classes:public_list')}?utm_source=discord&utm_medium=post")

        locations = _page_locations(page)
        assert locations, "expected at least one page_location queued into dataLayer"
        # Campaign attribution must survive; only free-text search is stripped.
        assert any("utm_source=discord" in location for location in locations)
