"""BDD specs for membership.signage.build_deck — the deck builder service.

The sharpest test is the privacy one: event slides are SITE-WIDE ONLY. A guild
meeting must never leak onto a wall monitor.
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, time, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from classes.factories import ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassSettings
from core.models import SiteConfiguration
from membership.cycle import get_cycle_context
from membership.qr import qr_svg
from membership.signage import SIGNAGE_CLASS_CAP, SIGNAGE_CLASS_DAYS, SIGNAGE_EVENT_CAP, build_deck, deck_hash
from tests.membership.factories import (
    CommunityEventFactory,
    GuildAnnouncementFactory,
    GuildFactory,
    SlideshowSlideFactory,
    SlideshowZoneFactory,
)

pytestmark = pytest.mark.django_db


# Every self-building block ships default ON, so a spec that wants to look at ONE block
# has to switch the rest off or the deck arrives full. _config does that for you: all
# blocks off, then your overrides on top.
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


def _config(**fields) -> SiteConfiguration:
    config = SiteConfiguration.load()
    for flag in _GENERATED_FLAGS:
        setattr(config, flag, False)
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

    def describe_evening_local_events():
        def it_still_shows_a_site_wide_event_later_the_same_local_evening():
            # 7pm Portland is 02:00Z the NEXT day. Bucketing the window on the UTC date
            # rolled it forward at ~5pm local and dropped every event still to come that
            # evening off the wall — precisely when the lobby has people standing in it.
            _config(signage_show_events=True)
            zone = SlideshowZoneFactory()
            # Both instants are built in UTC on purpose: timezone.now() returns a
            # UTC-aware datetime in production, and .date() on one of those yields the
            # UTC date. Passing a LOCAL-aware datetime here would hide the very bug this
            # pins, because .date() reads whatever tzinfo the object carries.
            event_start = datetime(2026, 9, 16, 3, 0, tzinfo=UTC)  # 8pm Sep 15 Portland
            CommunityEventFactory(
                community=True,
                title="Evening Potluck",
                starts_at=event_start,
                ends_at=event_start + timedelta(hours=2),
            )

            seven_pm_local = datetime(2026, 9, 16, 2, 0, tzinfo=UTC)  # 7pm Sep 15 Portland
            assert seven_pm_local.date() != timezone.localtime(seven_pm_local).date(), (
                "the fixture must straddle the UTC/local date boundary or it proves nothing"
            )
            with patch("django.utils.timezone.now", return_value=seven_pm_local):
                deck = build_deck(zone)

            assert "Evening Potluck" in [vm.title for vm in deck]

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

    def describe_duration():
        def it_uses_the_global_default_for_every_slide():
            _config(signage_show_events=False, signage_default_slide_seconds=15)
            zone = SlideshowZoneFactory()
            SlideshowSlideFactory()
            deck = build_deck(zone)
            assert deck[0].duration_seconds == 15

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


def describe_class_slides():
    """This week's classes — one slide per offering, deduped and capped."""

    def _published_offering(**fields):
        return ClassOfferingFactory(status="published", is_private=False, **fields)

    def it_adds_a_slide_for_a_class_inside_the_window():
        _config(signage_show_classes=True)
        zone = SlideshowZoneFactory()
        offering = _published_offering(title="Intro To Lathe")
        # 6pm local two days out — 01:00Z the day AFTER that, so a meta built off UTC
        # would name the wrong weekday and date on the wall.
        local_start = (timezone.localtime() + timedelta(days=2)).replace(hour=18, minute=0, second=0, microsecond=0)
        ClassSessionFactory(class_offering=offering, starts_at=local_start, ends_at=local_start + timedelta(hours=2))
        deck = build_deck(zone)
        class_vms = [vm for vm in deck if vm.kind == "class"]
        assert [vm.title for vm in class_vms] == ["Intro To Lathe"]
        assert class_vms[0].qr_svg is not None
        assert class_vms[0].meta == f"{local_start.strftime('%a, %b')} {local_start.day} · 6:00 PM"
        assert "://" not in class_vms[0].url_display

    def it_ignores_a_class_past_the_window():
        _config(signage_show_classes=True)
        zone = SlideshowZoneFactory()
        offering = _published_offering(title="Far Off Class")
        start = timezone.now() + timedelta(days=SIGNAGE_CLASS_DAYS + 3)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        deck = build_deck(zone)
        assert "Far Off Class" not in [vm.title for vm in deck]

    def it_renders_a_multi_session_class_once():
        _config(signage_show_classes=True)
        zone = SlideshowZoneFactory()
        offering = _published_offering(title="Two Night Series")
        for day in (1, 3):
            start = timezone.now() + timedelta(days=day)
            ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        deck = build_deck(zone)
        assert [vm.title for vm in deck if vm.kind == "class"] == ["Two Night Series"]

    def it_caps_the_block():
        _config(signage_show_classes=True)
        zone = SlideshowZoneFactory()
        for index in range(SIGNAGE_CLASS_CAP + 3):
            offering = _published_offering(title=f"Class {index}")
            start = timezone.now() + timedelta(hours=index + 1)
            ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        deck = build_deck(zone)
        assert len([vm for vm in deck if vm.kind == "class"]) == SIGNAGE_CLASS_CAP

    def it_never_shows_a_private_or_unpublished_class():
        _config(signage_show_classes=True)
        zone = SlideshowZoneFactory()
        hidden = [
            ClassOfferingFactory(title="Draft Class", status="draft", is_private=False),
            ClassOfferingFactory(title="Private Class", status="published", is_private=True),
        ]
        for offering in hidden:
            start = timezone.now() + timedelta(days=1)
            ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        deck = build_deck(zone)
        titles = [vm.title for vm in deck]
        assert "Draft Class" not in titles
        assert "Private Class" not in titles

    def it_adds_nothing_when_switched_off():
        _config(signage_show_classes=False)
        zone = SlideshowZoneFactory()
        offering = _published_offering(title="Intro To Lathe")
        start = timezone.now() + timedelta(days=2)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        deck = build_deck(zone)
        assert not any(vm.kind == "class" for vm in deck)


def describe_guilds_slide():
    def it_lists_every_visible_guild_on_one_slide():
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        GuildFactory(name="Blacksmiths")
        GuildFactory(name="Weavers")
        deck = build_deck(zone)
        guild_vms = [vm for vm in deck if vm.kind == "guilds"]
        assert len(guild_vms) == 1  # ONE slide, not one per guild
        assert "Blacksmiths" in guild_vms[0].body
        assert "Weavers" in guild_vms[0].body
        assert guild_vms[0].qr_svg is not None

    def it_adds_no_slide_when_there_are_no_visible_guilds():
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        deck = build_deck(zone)
        assert not any(vm.kind == "guilds" for vm in deck)

    def it_adds_nothing_when_switched_off():
        _config(signage_show_guilds=False)
        zone = SlideshowZoneFactory()
        GuildFactory(name="Blacksmiths")
        deck = build_deck(zone)
        assert not any(vm.kind == "guilds" for vm in deck)


def describe_calendar_slide():
    def _grid(zone) -> tuple:
        return next(vm for vm in build_deck(zone) if vm.kind == "calendar").calendar_days

    def _count_for(days: tuple, day: int) -> int:
        return next(cell.event_count for cell in days if cell.day == day)

    def it_covers_the_month_with_padding_cells():
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        days = _grid(zone)
        today = timezone.localdate()
        last_day = calendar.monthrange(today.year, today.month)[1]
        assert len(days) % 7 == 0  # whole weeks
        assert [cell.day for cell in days if cell.day] == list(range(1, last_day + 1))
        # Padding cells carry day=0 and nothing else — that is the contract the grid renders on.
        assert all(cell.event_count == 0 and not cell.is_today for cell in days if cell.day == 0)
        assert [cell.day for cell in days if cell.is_today] == [today.day]

    def it_dots_a_day_that_has_a_site_wide_event():
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        today = timezone.localdate()
        start = timezone.make_aware(datetime.combine(today, time(hour=13)))
        CommunityEventFactory(community=True, starts_at=start, ends_at=start + timedelta(hours=1))
        assert _count_for(_grid(zone), today.day) == 1

    def it_does_not_dot_a_day_that_only_has_a_guild_meeting():
        # The privacy rule: a private guild meeting must never influence a lobby wall,
        # not even as an anonymous dot. Mirrors the event-slide privacy assertion.
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        today = timezone.localdate()
        start = timezone.make_aware(datetime.combine(today, time(hour=13)))
        CommunityEventFactory(title="Woodshop Members Only", starts_at=start, ends_at=start + timedelta(hours=1))
        assert _count_for(_grid(zone), today.day) == 0

    def it_dots_the_local_day_of_a_6pm_event_on_the_last_day_of_the_month():
        # 6pm Portland is 01:00Z the NEXT day, which a naive .date() drops on the wrong
        # cell — and on the last of the month, out of the grid entirely.
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        today = timezone.localdate()
        last_day = calendar.monthrange(today.year, today.month)[1]
        last = today.replace(day=last_day)
        start = timezone.make_aware(datetime.combine(last, time(hour=18)))
        CommunityEventFactory(community=True, starts_at=start, ends_at=start + timedelta(hours=1))
        assert _count_for(_grid(zone), last_day) == 1

    def it_dots_a_class_earlier_in_the_month():
        # upcoming_public() is future-only; the grid uses public_between so the elapsed
        # half of the month is not blank.
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        today = timezone.localdate()
        first = today.replace(day=1)
        start = timezone.make_aware(datetime.combine(first, time(hour=12)))
        offering = ClassOfferingFactory(title="First Of The Month", status="published", is_private=False)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        assert _count_for(_grid(zone), 1) == 1

    def it_adds_nothing_when_switched_off():
        _config(signage_show_calendar=False)
        zone = SlideshowZoneFactory()
        deck = build_deck(zone)
        assert not any(vm.kind == "calendar" for vm in deck)


def describe_voting_slide():
    def it_names_the_cycle_and_carries_a_qr():
        _config(signage_show_voting=True)
        zone = SlideshowZoneFactory()
        vm = next(vm for vm in build_deck(zone) if vm.kind == "voting")
        assert vm.title == "Guild Funding Vote"
        assert "Voting closes" in vm.meta
        assert get_cycle_context()["current_cycle_label"] in vm.meta
        assert vm.qr_svg is not None

    def it_adds_nothing_when_switched_off():
        _config(signage_show_voting=False)
        zone = SlideshowZoneFactory()
        assert not any(vm.kind == "voting" for vm in build_deck(zone))


def describe_directory_slide():
    def it_renders_with_a_qr():
        _config(signage_show_directory=True)
        zone = SlideshowZoneFactory()
        vm = next(vm for vm in build_deck(zone) if vm.kind == "directory")
        assert vm.title == "Member Directory"
        assert vm.qr_svg is not None
        assert "://" not in vm.url_display

    def it_adds_nothing_when_switched_off():
        _config(signage_show_directory=False)
        zone = SlideshowZoneFactory()
        assert not any(vm.kind == "directory" for vm in build_deck(zone))


def describe_teach_slide():
    def it_uses_the_host_a_workshop_cta_copy():
        _config(signage_show_teach=True)
        zone = SlideshowZoneFactory()
        class_settings = ClassSettings.load()
        class_settings.teach_page_cta_title = "Got Something To Share?"
        class_settings.teach_page_cta_line = "Tell us what you have in mind."
        class_settings.save()
        vm = next(vm for vm in build_deck(zone) if vm.kind == "teach")
        assert vm.title == "Got Something To Share?"
        assert vm.body == "Tell us what you have in mind."
        assert vm.qr_svg is not None

    def it_falls_back_to_the_page_title_and_lead():
        _config(signage_show_teach=True)
        zone = SlideshowZoneFactory()
        class_settings = ClassSettings.load()
        class_settings.teach_page_cta_title = ""
        class_settings.teach_page_cta_line = ""
        class_settings.teach_page_title = "Host A Workshop"
        class_settings.teach_page_lead = "Share what you know."
        class_settings.save()
        vm = next(vm for vm in build_deck(zone) if vm.kind == "teach")
        assert vm.title == "Host A Workshop"
        assert vm.body == "Share what you know."

    def it_adds_no_slide_when_every_copy_field_is_blank():
        _config(signage_show_teach=True)
        zone = SlideshowZoneFactory()
        class_settings = ClassSettings.load()
        class_settings.teach_page_cta_title = ""
        class_settings.teach_page_cta_line = ""
        class_settings.teach_page_title = ""
        class_settings.teach_page_lead = ""
        class_settings.save()
        assert not any(vm.kind == "teach" for vm in build_deck(zone))

    def it_adds_nothing_when_switched_off():
        _config(signage_show_teach=False)
        zone = SlideshowZoneFactory()
        assert not any(vm.kind == "teach" for vm in build_deck(zone))


def describe_tour_slide():
    def it_renders_with_a_qr_to_the_booking_link():
        _config(signage_show_tour=True, signage_tour_url="https://www.pastlives.space/tours")
        zone = SlideshowZoneFactory()
        vm = next(vm for vm in build_deck(zone) if vm.kind == "tour")
        assert vm.title == "Book a Tour"
        assert vm.qr_svg is not None
        assert vm.url_display == "www.pastlives.space/tours"

    def it_encodes_the_configured_link_in_the_qr():
        # The one generated slide whose destination is admin-typed rather than reversed, so
        # the QR has to carry THAT url and not a default. qr_svg embeds no text, so compare
        # against the same helper's output for the configured link.
        _config(signage_show_tour=True, signage_tour_url="https://example.org/visit")
        zone = SlideshowZoneFactory()
        vm = next(vm for vm in build_deck(zone) if vm.kind == "tour")
        assert vm.qr_svg == qr_svg("https://example.org/visit")
        assert vm.qr_svg != qr_svg("https://www.pastlives.space/tours")

    def it_adds_no_slide_when_the_link_is_blank():
        _config(signage_show_tour=True, signage_tour_url="")
        zone = SlideshowZoneFactory()
        assert not any(vm.kind == "tour" for vm in build_deck(zone))

    def it_adds_nothing_when_switched_off():
        _config(signage_show_tour=False, signage_tour_url="https://www.pastlives.space/tours")
        zone = SlideshowZoneFactory()
        assert not any(vm.kind == "tour" for vm in build_deck(zone))


def describe_generated_block_order():
    def it_runs_the_blocks_in_the_fixed_order_after_the_admins_own_slides():
        config = _config()
        for flag in _GENERATED_FLAGS:
            setattr(config, flag, True)
        config.save()
        zone = SlideshowZoneFactory()
        SlideshowSlideFactory(title="A tip about the space", sort_order=0)
        GuildFactory(name="Blacksmiths")
        CommunityEventFactory(community=True, title="Community Potluck")
        offering = ClassOfferingFactory(title="Intro To Lathe", status="published", is_private=False)
        start = timezone.now() + timedelta(days=1)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        kinds = [vm.kind for vm in build_deck(zone)]
        assert kinds == ["custom", "class", "event", "guilds", "calendar", "voting", "directory", "teach", "tour"]


def describe_deck_hash_with_a_calendar():
    def it_changes_when_a_day_gains_its_first_event():
        # Without the grid in the digest, a day going from 0 to 1 event never swaps the deck.
        config = _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        before = deck_hash(build_deck(zone), config)
        today = timezone.localdate()
        start = timezone.make_aware(datetime.combine(today, time(hour=13)))
        CommunityEventFactory(community=True, starts_at=start, ends_at=start + timedelta(hours=1))
        assert deck_hash(build_deck(zone), config) != before
