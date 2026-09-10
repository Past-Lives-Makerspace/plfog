"""BDD specs for membership.signage.build_deck — the deck builder service.

The sharpest test is the privacy one: event slides are SITE-WIDE ONLY. A guild
meeting must never leak onto a wall monitor.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from django.utils.formats import date_format

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassSettings
from core.models import SiteConfiguration
from membership.cycle import get_cycle_context
from membership.qr import qr_svg
from membership.signage import (
    SIGNAGE_AGENDA_CAP,
    SIGNAGE_CLASS_CAP,
    SIGNAGE_CLASS_DAYS,
    SIGNAGE_EVENT_CAP,
    SIGNAGE_GUILD_ABOUT_CHARS,
    SIGNAGE_GUILD_HORIZON_DAYS,
    build_deck,
    deck_hash,
)
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


def describe_guild_slides():
    def _guild_vms(zone) -> list:
        return [vm for vm in build_deck(zone) if vm.kind == "guild"]

    def it_gives_each_visible_guild_its_own_slide():
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        GuildFactory(name="Blacksmiths")
        GuildFactory(name="Weavers")
        titles = [vm.title for vm in _guild_vms(zone)]
        assert titles == ["Blacksmiths", "Weavers"]  # one slide EACH, alphabetical
        assert all(vm.qr_svg is not None for vm in _guild_vms(zone))

    def it_labels_the_next_thing_so_it_does_not_read_as_a_tagline():
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        guild = GuildFactory(name="Blacksmiths")
        start = timezone.now() + timedelta(days=3)
        CommunityEventFactory(guild=guild, title="Forge Night", starts_at=start, ends_at=start + timedelta(hours=2))
        vm = next(vm for vm in _guild_vms(zone) if vm.title == "Blacksmiths")
        assert vm.meta_lead == "Next up"

    def it_carries_no_lead_on_the_about_fallback():
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        GuildFactory(name="Blacksmiths", about="Hot metal.")
        vm = next(vm for vm in _guild_vms(zone) if vm.title == "Blacksmiths")
        assert vm.meta_lead == ""

    def it_names_the_guilds_next_meeting():
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        guild = GuildFactory(name="Blacksmiths")
        start = timezone.now() + timedelta(days=3)
        CommunityEventFactory(guild=guild, title="Forge Night", starts_at=start, ends_at=start + timedelta(hours=2))
        vm = next(vm for vm in _guild_vms(zone) if vm.title == "Blacksmiths")
        assert vm.meta == "Forge Night"
        assert vm.body == date_format(timezone.localtime(start), "D, M j · g:i A")

    def it_names_a_guild_class_when_it_lands_first():
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        guild = GuildFactory(name="Blacksmiths")
        later = timezone.now() + timedelta(days=9)
        CommunityEventFactory(guild=guild, title="Forge Night", starts_at=later, ends_at=later + timedelta(hours=2))
        sooner = timezone.now() + timedelta(days=2)
        offering = ClassOfferingFactory(
            title="Make A Hook",
            status="published",
            is_private=False,
            category=CategoryFactory(guild=guild),
        )
        ClassSessionFactory(class_offering=offering, starts_at=sooner, ends_at=sooner + timedelta(hours=2))
        vm = next(vm for vm in _guild_vms(zone) if vm.title == "Blacksmiths")
        assert vm.meta == "Make A Hook"

    def it_falls_back_to_the_about_copy_when_nothing_is_coming_up():
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        GuildFactory(name="Blacksmiths", about="We hit hot metal with hammers. Everyone welcome.")
        vm = next(vm for vm in _guild_vms(zone) if vm.title == "Blacksmiths")
        assert vm.meta == ""
        assert vm.body == "We hit hot metal with hammers. Everyone welcome."

    def it_never_names_an_unpublished_meeting():
        # A member proposal awaiting review is not announced to a lobby. The widened
        # visibility covers PUBLISHED events only.
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        guild = GuildFactory(name="Blacksmiths", about="Hot metal.")
        start = timezone.now() + timedelta(days=3)
        CommunityEventFactory(
            guild=guild,
            pending=True,
            title="Unapproved Forge Night",
            starts_at=start,
            ends_at=start + timedelta(hours=2),
        )
        vm = next(vm for vm in _guild_vms(zone) if vm.title == "Blacksmiths")
        assert vm.meta == ""
        assert "Unapproved" not in vm.body

    def it_never_names_a_private_or_draft_class():
        # The class half of the visibility gate. The meeting half is pinned by
        # it_never_names_an_unpublished_meeting; without this, weakening
        # upcoming_public() puts a draft private class on a public lobby wall.
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        guild = GuildFactory(name="Blacksmiths", about="Hot metal.")
        soon = timezone.now() + timedelta(days=2)
        offering = ClassOfferingFactory(
            title="Secret Draft Class",
            status="draft",
            is_private=True,
            category=CategoryFactory(guild=guild),
        )
        ClassSessionFactory(class_offering=offering, starts_at=soon, ends_at=soon + timedelta(hours=2))
        vm = next(vm for vm in _guild_vms(zone) if vm.title == "Blacksmiths")
        assert vm.meta == ""
        assert vm.body == "Hot metal."

    def it_never_names_a_class_that_already_happened():
        # .first() on a start-ordered queryset picks the OLDEST row, so a gate that lets past
        # sessions through advertises last year's class as this guild's next thing.
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        guild = GuildFactory(name="Blacksmiths", about="Hot metal.")
        long_ago = timezone.now() - timedelta(days=400)
        offering = ClassOfferingFactory(
            title="Last Years Class",
            status="published",
            is_private=False,
            category=CategoryFactory(guild=guild),
        )
        ClassSessionFactory(class_offering=offering, starts_at=long_ago, ends_at=long_ago + timedelta(hours=2))
        vm = next(vm for vm in _guild_vms(zone) if vm.title == "Blacksmiths")
        assert vm.meta == ""
        assert vm.body == "Hot metal."

    def it_never_puts_one_guilds_meeting_on_another_guilds_slide():
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        blacksmiths = GuildFactory(name="Blacksmiths")
        GuildFactory(name="Weavers", about="Yarn.")
        start = timezone.now() + timedelta(days=3)
        CommunityEventFactory(
            guild=blacksmiths, title="Forge Night", starts_at=start, ends_at=start + timedelta(hours=2)
        )
        weavers = next(vm for vm in _guild_vms(zone) if vm.title == "Weavers")
        assert "Forge Night" not in weavers.meta
        assert "Forge Night" not in weavers.body

    def it_ignores_a_meeting_past_the_horizon():
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        guild = GuildFactory(name="Blacksmiths", about="Hot metal.")
        start = timezone.now() + timedelta(days=SIGNAGE_GUILD_HORIZON_DAYS + 5)
        CommunityEventFactory(guild=guild, title="Far Off Forge", starts_at=start, ends_at=start + timedelta(hours=2))
        vm = next(vm for vm in _guild_vms(zone) if vm.title == "Blacksmiths")
        assert vm.meta == ""
        assert vm.body == "Hot metal."

    def it_truncates_a_long_about():
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        GuildFactory(name="Blacksmiths", about="hammer " * 100)
        vm = next(vm for vm in _guild_vms(zone) if vm.title == "Blacksmiths")
        assert len(vm.body) <= SIGNAGE_GUILD_ABOUT_CHARS

    def it_adds_no_slide_when_there_are_no_visible_guilds():
        _config(signage_show_guilds=True)
        zone = SlideshowZoneFactory()
        assert _guild_vms(zone) == []

    def it_adds_nothing_when_switched_off():
        _config(signage_show_guilds=False)
        zone = SlideshowZoneFactory()
        GuildFactory(name="Blacksmiths")
        assert _guild_vms(zone) == []


def describe_whats_on_slide():
    # Noon Sep 15 Portland. Every instant below is built in UTC and the clock is frozen,
    # so "still to come", "already happened" and the month boundary are all decided by the
    # fixture rather than by what time the suite happens to run.
    NOON_SEP_15 = datetime(2026, 9, 15, 19, 0, tzinfo=UTC)

    def _deck(zone, now=NOON_SEP_15) -> list:
        with patch("django.utils.timezone.now", return_value=now):
            return build_deck(zone)

    def _entries(zone, now=NOON_SEP_15) -> tuple:
        return next(vm for vm in _deck(zone, now) if vm.kind == "calendar").entries

    def _titles(zone, now=NOON_SEP_15) -> list:
        return [entry.title for entry in _entries(zone, now)]

    def _event(title: str, start: datetime, **fields):
        return CommunityEventFactory(
            community=True, title=title, starts_at=start, ends_at=start + timedelta(hours=1), **fields
        )

    def it_names_what_is_still_to_come_this_month():
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        start = datetime(2026, 9, 20, 1, 0, tzinfo=UTC)  # 6pm Sep 19 Portland
        _event("Community Potluck", start)
        entry = next(e for e in _entries(zone) if e.title == "Community Potluck")
        assert entry.when_display == date_format(timezone.localtime(start), "D j M")
        assert entry.time_display == date_format(timezone.localtime(start), "g:i A")

    def it_orders_soonest_first():
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        _event("Later", datetime(2026, 9, 22, 1, 0, tzinfo=UTC))
        _event("Sooner", datetime(2026, 9, 18, 1, 0, tzinfo=UTC))
        assert _titles(zone) == ["Sooner", "Later"]

    def it_orders_a_class_and_an_event_against_each_other():
        # it_orders_soonest_first uses two events, which CommunityEvent.Meta.ordering already
        # sorts — so it passes with the sort deleted. Only a fixture that crosses the two
        # sources pins it: events are appended first, classes second.
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        _event("Late Event", datetime(2026, 9, 25, 19, 0, tzinfo=UTC))
        early = datetime(2026, 9, 18, 19, 0, tzinfo=UTC)
        offering = ClassOfferingFactory(title="Early Class", status="published", is_private=False)
        ClassSessionFactory(class_offering=offering, starts_at=early, ends_at=early + timedelta(hours=2))
        assert _titles(zone) == ["Early Class", "Late Event"]

    def it_never_names_a_guild_meeting():
        # The privacy rule for the SITE-WIDE list. A guild's meeting appears on that guild's
        # own slide, attributed, and nowhere else.
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        start = datetime(2026, 9, 20, 1, 0, tzinfo=UTC)
        event = CommunityEventFactory(
            title="Woodshop Members Only", starts_at=start, ends_at=start + timedelta(hours=1)
        )
        assert event.guild is not None  # the factory default IS a guild meeting
        assert not any(vm.kind == "calendar" for vm in _deck(zone))

    def it_leaves_out_something_earlier_the_same_day():
        # Same DAY as the frozen clock, two hours before it. A month-window filter alone lets
        # this through — only the `occurrence >= now` comparison drops it. A fixture on an
        # earlier date would be excluded by the window first and pin nothing.
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        this_morning = datetime(2026, 9, 15, 17, 0, tzinfo=UTC)  # 10am Sep 15 Portland
        assert this_morning < NOON_SEP_15 and this_morning.date() == NOON_SEP_15.date(), (
            "the fixture must sit earlier on the SAME day as the frozen clock or it proves nothing"
        )
        _event("Already Over", this_morning)
        assert not any(vm.kind == "calendar" for vm in _deck(zone))

    def it_keeps_a_6pm_event_on_the_last_day_of_the_month():
        # 6pm Portland on Sep 30 is 01:00Z on Oct 1. The CLOCK is frozen there too, not just the
        # fixture: derive the month from timezone.now().date() instead of localtime().date() and
        # "this month" becomes October, so the last evening of September empties off the wall —
        # exactly when the lobby has people standing in it. A clock frozen at midday would agree
        # in both timezones and pin nothing.
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        six_pm_sep_30 = datetime(2026, 10, 1, 1, 0, tzinfo=UTC)
        assert six_pm_sep_30.date().month != timezone.localtime(six_pm_sep_30).date().month, (
            "the fixture must straddle the month boundary in UTC or it proves nothing"
        )
        _event("Last Night Of The Month", six_pm_sep_30 + timedelta(hours=1))
        titles = _titles(zone, now=six_pm_sep_30)
        assert titles == ["Last Night Of The Month"]

    def it_caps_the_list():
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        for day in range(16, 16 + SIGNAGE_AGENDA_CAP + 2):
            _event(f"Thing on {day}", datetime(2026, 9, day, 20, 0, tzinfo=UTC))
        entries = _entries(zone)
        assert len(entries) == SIGNAGE_AGENDA_CAP
        assert entries[0].title == "Thing on 16"  # the cap keeps the SOONEST, not the first found

    def it_names_a_public_class_alongside_the_events():
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        start = datetime(2026, 9, 21, 19, 0, tzinfo=UTC)
        offering = ClassOfferingFactory(title="Intro To Lathe", status="published", is_private=False)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        assert "Intro To Lathe" in _titles(zone)

    def it_shows_one_line_when_a_class_is_also_on_the_calendar_by_hand():
        # A lead can put their own class on the community calendar as well. Both sources
        # then carry the same instant and title, and the same row twice reads as broken.
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        start = datetime(2026, 9, 21, 19, 0, tzinfo=UTC)
        offering = ClassOfferingFactory(title="Intro To Lathe", status="published", is_private=False)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        _event("Intro To Lathe", start)
        assert _titles(zone) == ["Intro To Lathe"]

    def it_adds_no_slide_when_nothing_is_left_this_month():
        _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        assert not any(vm.kind == "calendar" for vm in _deck(zone))

    def it_adds_nothing_when_switched_off():
        _config(signage_show_calendar=False)
        zone = SlideshowZoneFactory()
        _event("Community Potluck", datetime(2026, 9, 20, 1, 0, tzinfo=UTC))
        assert not any(vm.kind == "calendar" for vm in _deck(zone))


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
        # Frozen mid-September, so the What's On block always has a line left in the month —
        # on the 30th a relative fixture would push everything past the month end and the
        # block would correctly vanish, making the order assertion fail once a month.
        now = datetime(2026, 9, 15, 19, 0, tzinfo=UTC)
        event_start = datetime(2026, 9, 18, 1, 0, tzinfo=UTC)
        CommunityEventFactory(
            community=True, title="Community Potluck", starts_at=event_start, ends_at=event_start + timedelta(hours=1)
        )
        offering = ClassOfferingFactory(title="Intro To Lathe", status="published", is_private=False)
        start = datetime(2026, 9, 16, 19, 0, tzinfo=UTC)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
        with patch("django.utils.timezone.now", return_value=now):
            kinds = [vm.kind for vm in build_deck(zone)]
        assert kinds == ["custom", "class", "event", "guild", "calendar", "voting", "directory", "teach", "tour"]


def describe_deck_hash_with_a_whats_on_list():
    def it_changes_when_the_list_gains_a_line():
        # The deck must already CONTAIN a What's On slide before and after, or the hash differs
        # on slide membership and the entries digest is never exercised: an empty month has no
        # calendar slide at all, so going 0 -> 1 slides would pass with the digest deleted.
        # Every other field on the VM (kind, title, body, meta, url, duration) is identical
        # across the two decks, so only the entries digest can tell them apart.
        config = _config(signage_show_calendar=True)
        zone = SlideshowZoneFactory()
        now = datetime(2026, 9, 15, 19, 0, tzinfo=UTC)
        first = datetime(2026, 9, 18, 1, 0, tzinfo=UTC)
        CommunityEventFactory(
            community=True, title="Open Shop Night", starts_at=first, ends_at=first + timedelta(hours=1)
        )
        with patch("django.utils.timezone.now", return_value=now):
            before_deck = build_deck(zone)
            before = deck_hash(before_deck, config)
            second = datetime(2026, 9, 20, 1, 0, tzinfo=UTC)
            CommunityEventFactory(
                community=True, title="Community Potluck", starts_at=second, ends_at=second + timedelta(hours=1)
            )
            after_deck = build_deck(zone)
            assert [vm.kind for vm in before_deck] == [vm.kind for vm in after_deck]  # same slides
            assert deck_hash(after_deck, config) != before
