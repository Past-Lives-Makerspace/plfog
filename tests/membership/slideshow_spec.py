"""BDD specs for the SlideshowZone / SlideshowSlide models (visibility, for_zone, urls, QR)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from membership.models import SlideshowSlide
from tests.membership.factories import (
    GuildAnnouncementFactory,
    SlideshowSlideFactory,
    SlideshowZoneFactory,
)

pytestmark = pytest.mark.django_db


def describe_SlideshowSlide():
    def describe_visible():
        def it_includes_an_enabled_slide_with_no_window():
            slide = SlideshowSlideFactory(is_enabled=True, starts_on=None, ends_on=None)
            assert slide in SlideshowSlide.objects.visible()

        def it_excludes_a_disabled_slide():
            slide = SlideshowSlideFactory(is_enabled=False)
            assert slide not in SlideshowSlide.objects.visible()

        def it_excludes_a_slide_starting_in_the_future():
            future = timezone.localdate() + timedelta(days=3)
            slide = SlideshowSlideFactory(starts_on=future)
            assert slide not in SlideshowSlide.objects.visible()

        def it_excludes_a_slide_that_already_ended():
            past = timezone.localdate() - timedelta(days=1)
            slide = SlideshowSlideFactory(ends_on=past)
            assert slide not in SlideshowSlide.objects.visible()

        def it_includes_a_slide_inside_its_window():
            today = timezone.localdate()
            slide = SlideshowSlideFactory(starts_on=today - timedelta(days=1), ends_on=today + timedelta(days=1))
            assert slide in SlideshowSlide.objects.visible()

        def describe_announcement_backed():
            def it_includes_a_slide_whose_announcement_is_published_and_active():
                ann = GuildAnnouncementFactory()  # PUBLISHED, no expiry
                slide = SlideshowSlideFactory(kind=SlideshowSlide.Kind.ANNOUNCEMENT, title="", announcement=ann)
                assert slide in SlideshowSlide.objects.visible()

            def it_excludes_a_slide_whose_announcement_is_unpublished():
                ann = GuildAnnouncementFactory(pending=True)
                slide = SlideshowSlideFactory(kind=SlideshowSlide.Kind.ANNOUNCEMENT, title="", announcement=ann)
                assert slide not in SlideshowSlide.objects.visible()

            def it_excludes_a_slide_whose_announcement_has_expired():
                ann = GuildAnnouncementFactory(expires_at=timezone.localdate() - timedelta(days=1))
                slide = SlideshowSlideFactory(kind=SlideshowSlide.Kind.ANNOUNCEMENT, title="", announcement=ann)
                assert slide not in SlideshowSlide.objects.visible()

            def it_still_hides_an_announcement_slide_with_no_announcement_set():
                slide = SlideshowSlideFactory(kind=SlideshowSlide.Kind.ANNOUNCEMENT, title="", announcement=None)
                assert slide not in SlideshowSlide.objects.visible()

    def describe_for_zone():
        def it_includes_all_zones_slides_for_any_zone():
            zone_a = SlideshowZoneFactory()
            all_zones_slide = SlideshowSlideFactory(zone=None)
            assert all_zones_slide in SlideshowSlide.objects.for_zone(zone_a)

        def it_includes_a_slide_pinned_to_that_zone():
            zone_a = SlideshowZoneFactory()
            pinned = SlideshowSlideFactory(zone=zone_a)
            assert pinned in SlideshowSlide.objects.for_zone(zone_a)

        def it_excludes_a_slide_pinned_to_another_zone():
            zone_a = SlideshowZoneFactory()
            zone_b = SlideshowZoneFactory()
            other = SlideshowSlideFactory(zone=zone_b)
            assert other not in SlideshowSlide.objects.for_zone(zone_a)

    def describe_str():
        def it_uses_the_title_when_set():
            slide = SlideshowSlideFactory(title="Welcome")
            assert str(slide) == "Welcome"

        def it_falls_back_to_the_announcement_when_no_title():
            ann = GuildAnnouncementFactory(title="Big News")
            slide = SlideshowSlideFactory(kind=SlideshowSlide.Kind.ANNOUNCEMENT, title="", announcement=ann)
            assert "Big News" in str(slide)


def describe_SlideshowZone():
    @override_settings(SIGNAGE_BASE_URL="https://slideshow.pastlives.space")
    def it_builds_an_absolute_player_url_from_the_slug():
        zone = SlideshowZoneFactory(slug="woodshop")
        assert zone.player_url == "https://slideshow.pastlives.space/woodshop/"

    @override_settings(SIGNAGE_BASE_URL="https://slideshow.pastlives.space")
    def it_renders_an_svg_qr_of_the_player_url():
        zone = SlideshowZoneFactory(slug="lobby")
        svg = zone.qr_svg()
        assert svg.startswith("<svg") or "<svg" in svg
        assert svg.strip() != ""

    def it_str_is_the_name():
        zone = SlideshowZoneFactory(name="Woodshop")
        assert str(zone) == "Woodshop"
