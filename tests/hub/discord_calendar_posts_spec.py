"""BDD specs for the #public-calendar Discord posts (weekly digest + new-event announcer).

HTTP is mocked with respx (never the models/DB): the service posts through the real
``core.integrations.discord_channel`` client against a mocked Discord messages endpoint.
"""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
import respx
from django.conf import settings
from django.utils import timezone

from core.models import SiteConfiguration
from hub import discord_calendar_posts as dcp
from membership.models import CalendarEvent, CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory

pytestmark = pytest.mark.django_db

CHANNEL_ID = "1309624926893768854"
_MESSAGES_URL = f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages"


def _enable_posts(channel_id: str = CHANNEL_ID) -> SiteConfiguration:
    config = SiteConfiguration.load()
    config.discord_calendar_posts_enabled = True
    config.discord_calendar_channel_id = channel_id
    config.save(update_fields=["discord_calendar_posts_enabled", "discord_calendar_channel_id"])
    return config


def _feed_event(title: str, *, days: float = 2, url: str = "", source: str = "general", **kwargs) -> CalendarEvent:
    now = timezone.now()
    defaults = dict(
        source=source,
        uid=f"uid-{title}",
        title=title,
        url=url,
        start_dt=now + timedelta(days=days),
        end_dt=now + timedelta(days=days, hours=2),
        fetched_at=now,
    )
    defaults.update(kwargs)
    return CalendarEvent.objects.create(**defaults)


def _sent_embeds(route: respx.Route) -> list[dict]:
    """Every embed across every message the mocked route received, in order."""
    embeds: list[dict] = []
    for call in route.calls:
        embeds.extend(json.loads(call.request.content)["embeds"])
    return embeds


def describe_build_weekly_digest_embeds():
    def it_groups_the_next_seven_days_by_day_with_feed_and_community_events():
        guild = GuildFactory(name="Woodshop")
        _feed_event("Forge Night", days=2, source="guild", guild=guild)
        now = timezone.now()
        CommunityEventFactory(
            community=True,
            title="Spring Mixer",
            starts_at=now + timedelta(days=4),
            ends_at=now + timedelta(days=4, hours=2),
        )

        embeds = dcp.build_weekly_digest_embeds(now)
        assert len(embeds) == 1
        description = embeds[0]["description"]
        assert "Forge Night" in description
        assert "Spring Mixer" in description
        # Grouped under each event's own local day header.
        day = timezone.localtime(now + timedelta(days=2)).strftime("%A, %B %-d")
        assert f"**{day}**" in description

    def it_excludes_classes_from_the_events_only_digest():
        # #public-calendar is events-only; classes belong in the #classes digest.
        _feed_event("Intro to Welding", days=3, source="classes", url="/classes/welding/")
        now = timezone.now()
        CommunityEventFactory(
            community=True,
            title="Spring Mixer",
            starts_at=now + timedelta(days=4),
            ends_at=now + timedelta(days=4, hours=2),
        )

        description = dcp.build_weekly_digest_embeds(now)[0]["description"]
        assert "Spring Mixer" in description
        assert "Intro to Welding" not in description

    def it_links_items_absolutely_and_footers_to_the_full_calendar():
        _feed_event("Open Forge", days=3, source="general", url="/events/open-forge/")
        now = timezone.now()
        event = CommunityEventFactory(
            community=True,
            title="Spring Mixer",
            starts_at=now + timedelta(days=4),
            ends_at=now + timedelta(days=4, hours=2),
        )

        description = dcp.build_weekly_digest_embeds(now)[0]["description"]
        assert f"[Open Forge]({settings.MEMBER_BASE_URL}/events/open-forge/)" in description
        assert f"[Spring Mixer]({event.absolute_url})" in description
        assert event.absolute_url.startswith("http")
        assert f"[See the full calendar →]({settings.MEMBER_BASE_URL}/calendar/)" in description

    def it_excludes_items_outside_the_seven_day_window():
        _feed_event("Next Month Gala", days=20)
        now = timezone.now()

        assert dcp.build_weekly_digest_embeds(now) == []

    def it_marks_an_all_day_event_as_all_day():
        _feed_event("Open House", days=2, all_day=True)
        description = dcp.build_weekly_digest_embeds(timezone.now())[0]["description"]
        assert "All day — Open House" in description

    def it_chunks_into_multiple_embeds_when_a_week_overflows_the_embed_cap():
        for i in range(30):
            _feed_event(f"Marathon session {i:02d} " + "x" * 300, days=2 + (i % 2) * 0.01)
        embeds = dcp.build_weekly_digest_embeds(timezone.now())
        assert len(embeds) > 1
        assert all(len(e["description"]) <= dcp.EMBED_DESCRIPTION_MAX for e in embeds)
        assert embeds[0]["title"].startswith("This week at Past Lives")
        assert "(continued)" in embeds[1]["title"]

    def it_leaves_standing_studio_hours_out_of_the_digest():
        now = timezone.now()
        _feed_event("Forge Night", days=2)
        # A weekly studio-hours block with an occurrence inside the digest window.
        CommunityEventFactory(
            studio_hours=True,
            title="Wood Guild Studio Hours",
            starts_at=now - timedelta(days=30),
            ends_at=now - timedelta(days=30) + timedelta(hours=3),
        )
        description = dcp.build_weekly_digest_embeds(now)[0]["description"]
        assert "Forge Night" in description
        assert "Wood Guild Studio Hours" not in description

    def it_excludes_a_feed_event_titled_studio_hours():
        # A guild's Google-calendar "open studio hours" block arrives as a feed event with
        # no event type — filter it by title so #public-calendar stays real events only.
        now = timezone.now()
        _feed_event("Art Framing open studio hours", days=2)
        CommunityEventFactory(
            community=True,
            title="Spring Mixer",
            starts_at=now + timedelta(days=3),
            ends_at=now + timedelta(days=3, hours=2),
        )
        description = dcp.build_weekly_digest_embeds(now)[0]["description"]
        assert "Spring Mixer" in description
        assert "Art Framing open studio hours" not in description


def describe_batch_embeds():
    def it_splits_batches_under_the_combined_six_thousand_char_message_cap():
        # A busy week: ~9,900 chars of digest → 3 embeds each under 4,096, but any two
        # together blow Discord's 6,000-per-MESSAGE combined cap. Assert on the built
        # payload batches — an HTTP mock can't catch a limit Discord enforces server-side.
        for i in range(30):
            _feed_event(f"Marathon session {i:02d} " + "x" * 300, days=2 + (i % 2) * 0.01)
        embeds = dcp.build_weekly_digest_embeds(timezone.now())
        assert sum(dcp._embed_chars(e) for e in embeds) > dcp.MESSAGE_EMBED_TOTAL_MAX  # the scenario is real

        batches = dcp._batch_embeds(embeds)
        assert len(batches) > 1
        for batch in batches:
            assert sum(dcp._embed_chars(e) for e in batch) <= dcp.MESSAGE_EMBED_TOTAL_MAX
            assert len(batch) <= 10
        # Nothing dropped or reordered — the batches re-concatenate to the original list.
        assert [e for batch in batches for e in batch] == embeds

    def it_keeps_a_small_digest_in_a_single_message():
        embeds = [{"title": "t", "description": "d" * 100}]
        assert dcp._batch_embeds(embeds) == [embeds]


def describe_post_weekly_digest():
    @respx.mock
    def it_noops_when_the_toggle_is_off():
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _feed_event("Forge Night")
        assert dcp.post_weekly_digest() == 0
        assert not route.called

    @respx.mock
    def it_noops_when_no_channel_id_is_set():
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts(channel_id="")
        _feed_event("Forge Night")
        assert dcp.post_weekly_digest() == 0
        assert not route.called

    @respx.mock
    def it_never_posts_an_empty_digest():
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        assert dcp.post_weekly_digest() == 0
        assert not route.called

    @respx.mock
    def it_posts_the_digest_and_returns_the_item_count(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        _feed_event("Forge Night", days=2)
        _feed_event("Print Party", days=3)

        assert dcp.post_weekly_digest() == 2
        assert route.call_count == 1
        embeds = _sent_embeds(route)
        assert "Forge Night" in embeds[0]["description"]
        assert "Print Party" in embeds[0]["description"]


def describe_announce_new_events():
    @respx.mock
    def it_noops_when_the_toggle_is_off():
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        event = _feed_event("Forge Night")
        assert dcp.announce_new_events() == 0
        assert not route.called
        event.refresh_from_db()
        assert event.channel_announced_at is None  # left for a future enabled run

    @respx.mock
    def it_posts_a_compact_embed_per_new_item_and_stamps_it(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        feed = _feed_event("Forge Night", days=2)
        now = timezone.now()
        community = CommunityEventFactory(
            community=True,
            title="Spring Mixer",
            starts_at=now + timedelta(days=4),
            ends_at=now + timedelta(days=4, hours=2),
        )

        assert dcp.announce_new_events() == 2
        assert route.call_count == 2  # one compact message per item
        embeds = _sent_embeds(route)
        assert [e["title"] for e in embeds] == ["Forge Night", "Spring Mixer"]
        assert "New on the calendar" in embeds[0]["description"]
        assert embeds[1]["url"] == community.absolute_url
        for obj in (feed, community):
            obj.refresh_from_db()
            assert obj.channel_announced_at is not None

    @respx.mock
    def it_never_announces_class_events(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        klass = _feed_event("Intro to Welding", days=3, source="classes", url="/classes/welding/")

        assert dcp.announce_new_events() == 0  # classes post to #classes, not #public-calendar
        assert not route.called
        klass.refresh_from_db()
        assert klass.channel_announced_at is None  # left unstamped by the events-only announcer

    @respx.mock
    def it_never_announces_the_same_event_twice(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        _feed_event("Forge Night", days=2)

        assert dcp.announce_new_events() == 1
        assert dcp.announce_new_events() == 0
        assert route.call_count == 1

    @respx.mock
    def it_skips_echoes_of_our_own_pushed_events(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        pushed = CommunityEventFactory(community=True, pushed=True, channel_announced_at=timezone.now())
        _feed_event("Echo Copy", days=2, uid=pushed.google_ical_uid)

        assert dcp.announce_new_events() == 0
        assert not route.called

    @respx.mock
    def it_skips_past_events_unpublished_events_and_studio_hours(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        _feed_event("Yesterday's Meetup", days=-1)
        CommunityEventFactory(community=True, pending=True, title="Unreviewed Proposal")
        CommunityEventFactory(studio_hours=True, title="Weekly Studio Hours")

        assert dcp.announce_new_events() == 0
        assert not route.called

    @respx.mock
    def it_never_announces_a_feed_event_titled_studio_hours(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        hours = _feed_event("Woodshop open studio hours", days=2)

        assert dcp.announce_new_events() == 0
        assert not route.called
        hours.refresh_from_db()
        assert hours.channel_announced_at is None

    @respx.mock
    def it_caps_at_ten_posts_and_silently_stamps_the_overflow(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        for i in range(13):
            _feed_event(f"Backlog {i:02d}", days=1 + i * 0.1)

        assert dcp.announce_new_events() == dcp.ANNOUNCE_CAP
        assert route.call_count == dcp.ANNOUNCE_CAP
        assert CalendarEvent.objects.filter(channel_announced_at__isnull=True).count() == 0  # overflow stamped too
        # A second run has nothing left — the overflow never floods the channel later.
        assert dcp.announce_new_events() == 0

    @respx.mock
    def it_collapses_a_recurring_feed_series_to_one_announcement_and_stamps_every_row(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        # A weekly feed series materializes one CalendarEvent row per occurrence, all
        # sharing a uid — the announcer must post its earliest occurrence once, not 5x.
        for week in range(5):
            _feed_event(
                "Weekly Repair Cafe",
                days=2 + week * 7,
                uid="uid-series",
                recurrence_id=f"occ-{week}",
            )

        assert dcp.announce_new_events() == 1
        assert route.call_count == 1
        embed = _sent_embeds(route)[0]
        assert embed["title"] == "Weekly Repair Cafe"
        earliest = CalendarEvent.objects.filter(uid="uid-series").order_by("start_dt").first()
        assert timezone.localtime(earliest.start_dt).strftime("%A, %B %-d") in embed["description"]
        # Every sibling occurrence row is stamped in the same pass — no drip-feed later.
        assert CalendarEvent.objects.filter(uid="uid-series", channel_announced_at__isnull=True).count() == 0
        assert dcp.announce_new_events() == 0

    @respx.mock
    def it_holds_an_event_past_the_three_month_horizon_then_announces_it_once_inside(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        # The nightly sync's leading edge: an occurrence ~5 months out, past the 90-day window.
        event = _feed_event("Tech Guild Meeting", days=150)

        assert dcp.announce_new_events() == 0  # held — not pre-announced a quarter early
        assert not route.called
        event.refresh_from_db()
        assert event.channel_announced_at is None  # left unstamped so a later run can post it

        # A later sync brings its next occurrence inside the window; now it announces.
        event.start_dt = timezone.now() + timedelta(days=80)
        event.end_dt = event.start_dt + timedelta(hours=1)
        event.save(update_fields=["start_dt", "end_dt"])

        assert dcp.announce_new_events() == 1
        assert route.call_count == 1

    @respx.mock
    def it_does_not_reannounce_a_series_leading_edge_that_lands_past_the_horizon(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        now = timezone.now()
        # The exact "February in August" case: a weekly series whose near occurrences already
        # went out, and the nightly sync just materialized a fresh far-edge occurrence. That
        # lone far row must not be announced on its own.
        _feed_event("Weekly Guild Meeting", days=3, uid="uid-guild", recurrence_id="near", channel_announced_at=now)
        far = _feed_event("Weekly Guild Meeting", days=150, uid="uid-guild", recurrence_id="far")

        assert dcp.announce_new_events() == 0
        assert not route.called
        far.refresh_from_db()
        assert far.channel_announced_at is None

    @respx.mock
    def it_still_announces_two_distinct_events_separately(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        _feed_event("Forge Night", days=2)
        _feed_event("Print Party", days=3)

        assert dcp.announce_new_events() == 2
        assert route.call_count == 2
        assert [e["title"] for e in _sent_embeds(route)] == ["Forge Night", "Print Party"]

    @respx.mock
    def it_announces_a_recurring_event_at_its_next_occurrence(settings):
        settings.DISCORD_BOT_TOKEN = "tok"
        route = respx.post(_MESSAGES_URL).mock(return_value=httpx.Response(200, json={}))
        _enable_posts()
        now = timezone.now()
        CommunityEventFactory(
            community=True,
            title="Monthly Social",
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=now - timedelta(days=30),
            ends_at=now - timedelta(days=30) + timedelta(hours=2),
        )

        assert dcp.announce_new_events() == 1
        when_line = _sent_embeds(route)[0]["description"]
        # Announced with an attendable (future/nowish) occurrence, not the month-old anchor.
        anchor_day = timezone.localtime(now - timedelta(days=30)).strftime("%B %-d")
        assert anchor_day not in when_line


def describe_when_formatting():
    def it_formats_a_same_day_event_with_a_time_range():
        start = timezone.now().replace(hour=18, minute=0, second=0, microsecond=0)
        text = dcp._when(start, start + timedelta(hours=2), False)
        local = timezone.localtime(start)
        assert local.strftime("%A, %B %-d") in text
        assert "–" in text

    def it_formats_an_all_day_event():
        start = timezone.now()
        assert dcp._when(start, start + timedelta(days=1), True).endswith("All day")

    def it_spells_out_both_days_for_an_overnight_event():
        start = timezone.now().replace(hour=22, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=12)
        text = dcp._when(start, end, False)
        assert timezone.localtime(end).strftime("%A, %B %-d") in text


def describe_next_community_start():
    def it_falls_back_to_the_anchor_when_no_occurrence_is_in_horizon():
        now = timezone.now()
        far_future = CommunityEventFactory(
            community=True,
            starts_at=now + timedelta(days=400),
            ends_at=now + timedelta(days=400, hours=2),
        )
        assert dcp._next_community_start(far_future, now) == far_future.starts_at
