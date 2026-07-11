"""BDD specs for membership.signage.build_deck — the deck builder service.

The sharpest test is the privacy one: event slides are SITE-WIDE ONLY. A guild
meeting must never leak onto a wall monitor.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from core.models import SiteConfiguration
from membership.signage import SIGNAGE_EVENT_CAP, build_deck
from tests.membership.factories import (
    CommunityEventFactory,
    GuildAnnouncementFactory,
    SlideshowSlideFactory,
    SlideshowZoneFactory,
)

pytestmark = pytest.mark.django_db


def _config(**fields) -> SiteConfiguration:
    config = SiteConfiguration.load()
    for name, value in fields.items():
        setattr(config, name, value)
    config.save()
    return config


def describe_build_deck():
    def describe_event_slides_privacy():
        def it_includes_site_wide_events_and_excludes_guild_meetings():
            _config(signage_show_events=True)
            zone = SlideshowZoneFactory()
            site_wide = CommunityEventFactory(community=True, title="Community Potluck")
            guild_meeting = CommunityEventFactory(title="Woodshop Members Only")  # guild set

            deck = build_deck(zone)
            titles = [vm.title for vm in deck]
            assert site_wide.title in titles
            assert guild_meeting.title not in titles

    def describe_signage_show_events_toggle():
        def it_adds_no_event_slides_when_disabled():
            _config(signage_show_events=False)
            zone = SlideshowZoneFactory()
            CommunityEventFactory(community=True)
            deck = build_deck(zone)
            assert not any(vm.kind == "event" for vm in deck)

    def describe_event_qr_and_learn_more():
        def it_always_puts_a_qr_on_every_event_slide():
            _config(signage_show_events=True)
            zone = SlideshowZoneFactory()
            CommunityEventFactory(community=True, title="Community Potluck")
            deck = build_deck(zone)
            event_vm = next(vm for vm in deck if vm.kind == "event")
            assert event_vm.qr_svg is not None
            assert "<svg" in event_vm.qr_svg

        def it_shows_a_scheme_stripped_learn_more_url_on_event_slides():
            _config(signage_show_events=True)
            zone = SlideshowZoneFactory()
            CommunityEventFactory(community=True, title="Community Potluck")
            deck = build_deck(zone)
            event_vm = next(vm for vm in deck if vm.kind == "event")
            assert event_vm.url_display
            assert "://" not in event_vm.url_display

        def it_gives_a_custom_slide_a_learn_more_url_even_without_a_qr():
            _config(signage_show_events=False)
            zone = SlideshowZoneFactory()
            SlideshowSlideFactory(show_qr=False, link_url="https://pastlives.app/calendar/")
            deck = build_deck(zone)
            assert deck[0].qr_svg is None
            assert deck[0].url_display == "pastlives.app/calendar"

    def describe_horizon_and_cap():
        def it_excludes_events_beyond_the_look_ahead_window():
            _config(signage_show_events=True, signage_event_days_ahead=30)
            zone = SlideshowZoneFactory()
            far = timezone.now() + timedelta(days=60)
            CommunityEventFactory(community=True, title="Far Off", starts_at=far, ends_at=far + timedelta(hours=1))
            deck = build_deck(zone)
            assert "Far Off" not in [vm.title for vm in deck]

        def it_caps_the_number_of_event_slides():
            _config(signage_show_events=True, signage_event_days_ahead=60)
            zone = SlideshowZoneFactory()
            for day in range(1, 11):  # 10 events, all in-window
                start = timezone.now() + timedelta(days=day)
                CommunityEventFactory(community=True, starts_at=start, ends_at=start + timedelta(hours=1))
            deck = build_deck(zone)
            assert len([vm for vm in deck if vm.kind == "event"]) == SIGNAGE_EVENT_CAP

    def describe_duration_fallback():
        def it_uses_the_global_default_when_a_slide_has_no_duration():
            _config(signage_show_events=False, signage_default_slide_seconds=15)
            zone = SlideshowZoneFactory()
            SlideshowSlideFactory(duration_seconds=None)
            deck = build_deck(zone)
            assert deck[0].duration_seconds == 15

        def it_honors_a_slide_specific_duration():
            _config(signage_show_events=False, signage_default_slide_seconds=15)
            zone = SlideshowZoneFactory()
            SlideshowSlideFactory(duration_seconds=25)
            deck = build_deck(zone)
            assert deck[0].duration_seconds == 25

    def describe_deck_order():
        def it_places_configured_slides_before_generated_event_slides():
            _config(signage_show_events=True)
            zone = SlideshowZoneFactory()
            SlideshowSlideFactory(title="A tip about the space", sort_order=0)
            CommunityEventFactory(community=True, title="Upcoming Event")
            deck = build_deck(zone)
            kinds = [vm.kind for vm in deck]
            first_event = kinds.index("event")
            # No custom slide appears after the first event slide.
            assert "custom" not in kinds[first_event:]

    def describe_announcement_slides():
        def it_pulls_the_linked_announcements_live_title_and_body():
            _config(signage_show_events=False)
            zone = SlideshowZoneFactory()
            ann = GuildAnnouncementFactory(title="Shop Closed Friday", body="See you Monday.")
            SlideshowSlideFactory(kind="announcement", title="", announcement=ann)
            deck = build_deck(zone)
            assert deck[0].title == "Shop Closed Friday"
            assert deck[0].body == "See you Monday."

    def describe_custom_qr():
        def it_renders_a_qr_when_show_qr_and_a_link_are_set():
            _config(signage_show_events=False)
            zone = SlideshowZoneFactory()
            SlideshowSlideFactory(show_qr=True, link_url="https://pastlives.space/x")
            deck = build_deck(zone)
            assert deck[0].qr_svg is not None
            assert "<svg" in deck[0].qr_svg

        def it_renders_no_qr_without_a_link():
            _config(signage_show_events=False)
            zone = SlideshowZoneFactory()
            SlideshowSlideFactory(show_qr=True, link_url="")
            deck = build_deck(zone)
            assert deck[0].qr_svg is None

    def describe_empty_deck():
        def it_returns_a_branded_holding_slide_when_nothing_is_configured():
            _config(signage_show_events=False)
            zone = SlideshowZoneFactory()
            deck = build_deck(zone)
            assert len(deck) == 1
            assert deck[0].kind == "holding"
            assert deck[0].title == "Past Lives Makerspace"
