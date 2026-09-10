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

# Every self-building signage block ships default ON; a spec that wants a deck of exactly
# what it configured has to switch them all off first.
_GENERATED_FLAGS = (
    "signage_show_events",
    "signage_show_classes",
    "signage_show_guilds",
    "signage_show_calendar",
    "signage_show_voting",
    "signage_show_directory",
    "signage_show_teach",
    "signage_show_tour",
)


def describe_signage_player():
    def it_renders_the_first_slide_and_auto_advances_to_the_second(live_server, page, settings):
        # Make the live host resolve to the signage surface (the autouse _e2e_settings
        # leaves SIGNAGE_HOSTS at its default, which localhost is not in).
        settings.SIGNAGE_HOSTS = [urlparse(live_server.url).hostname]

        # Keep the deck to exactly our two slides — every self-building block off.
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        for flag in _GENERATED_FLAGS:
            setattr(config, flag, False)
        # Every slide uses the global default now — keep it short so the rotation is easy to catch.
        config.signage_default_slide_seconds = 2
        config.save()

        zone = SlideshowZoneFactory(slug="woodshop", is_enabled=True)
        # Distinct-titled slides so the rotation window is easy to catch.
        SlideshowSlideFactory(zone=zone, title="First Slide", sort_order=0)
        SlideshowSlideFactory(zone=zone, title="Second Slide", sort_order=1)

        page.goto(f"{live_server.url}/{zone.slug}/")

        # First paint: slide 1 is server-rendered active (the wall is never blank waiting on JS).
        expect(page.locator("section.pl-sign-slide.is-active")).to_contain_text("First Slide")

        # Auto-advance: the Alpine timer moves the active class onto slide 2 within a cycle.
        page.wait_for_selector("section.pl-sign-slide.is-active:has-text('Second Slide')", timeout=8000)

        # The public deck carries no member chrome or PII (auth-parity is unit-tested; here
        # just confirm the kiosk renders the public deck, not the members hub).
        expect(page.locator("body")).not_to_contain_text("Admin View")
        expect(page.locator(".pl-bell__btn")).to_have_count(0)
