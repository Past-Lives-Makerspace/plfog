"""End-to-end: the public kiosk slideshow player rotates in a real browser.

The player is served ONLY on the signage surface, but ``live_server`` runs on
``localhost`` (which the harness routes to the members surface). So this spec
overrides ``SIGNAGE_HOSTS`` to include the live host, making ``localhost`` resolve
to the signage surface. The player is public — no login.

The sharp thing a browser proves that a unit test can't: the Alpine rotation
actually runs — the first slide server-renders ``is-active``, then the timer
advances the active class to the second slide. Run with ``pytest -m e2e``.
"""

from __future__ import annotations

from urllib.parse import urlparse

from playwright.sync_api import expect

from tests.membership.factories import SlideshowSlideFactory, SlideshowZoneFactory


def describe_signage_player():
    def it_renders_the_first_slide_and_auto_advances_to_the_second(live_server, page, settings):
        # Make the live host resolve to the signage surface (the autouse _e2e_settings
        # leaves SIGNAGE_HOSTS at its default, which localhost is not in).
        settings.SIGNAGE_HOSTS = [urlparse(live_server.url).hostname]

        # Keep the deck to exactly our two slides — no generated event slides.
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.signage_show_events = False
        config.save()

        zone = SlideshowZoneFactory(slug="woodshop", is_enabled=True)
        # Short, distinct-titled slides so the rotation window is easy to catch.
        SlideshowSlideFactory(zone=zone, title="First Slide", duration_seconds=2, sort_order=0)
        SlideshowSlideFactory(zone=zone, title="Second Slide", duration_seconds=2, sort_order=1)

        page.goto(f"{live_server.url}/{zone.slug}/")

        # First paint: slide 1 is server-rendered active (the wall is never blank waiting on JS).
        expect(page.locator("section.pl-sign-slide.is-active")).to_contain_text("First Slide")

        # Auto-advance: the Alpine timer moves the active class onto slide 2 within a cycle.
        page.wait_for_selector("section.pl-sign-slide.is-active:has-text('Second Slide')", timeout=8000)

        # The public deck carries no member chrome or PII (auth-parity is unit-tested; here
        # just confirm the kiosk renders the public deck, not the members hub).
        expect(page.locator("body")).not_to_contain_text("Admin View")
        expect(page.locator(".pl-bell__btn")).to_have_count(0)
