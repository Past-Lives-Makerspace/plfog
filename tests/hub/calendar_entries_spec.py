"""BDD specs for the synthetic calendar-entry wrapper."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

from classes.factories import CategoryFactory, ClassOfferingFactory, ClassSessionFactory
from classes.models import ClassOffering
from core.models import SiteConfiguration
from hub.calendar_entries import (
    CalendarEntry,
    calendar_subscribe_links,
    google_calendar_add_url,
    google_calendar_event_url,
    google_calendar_subscribe_url,
    google_target_feed_keys,
)
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory


def describe_google_calendar_subscribe_url():
    def it_builds_a_webcal_url_and_encodes_the_calendar_id():
        url = google_calendar_subscribe_url("abc123@group.calendar.google.com")
        assert url == "webcal://calendar.google.com/calendar/ical/abc123%40group.calendar.google.com/public/basic.ics"

    def it_returns_an_empty_string_for_a_blank_id():
        assert google_calendar_subscribe_url("") == ""


def _entry(**kwargs) -> CalendarEntry:
    now = timezone.now()
    base = {"pk": 1, "title": "x", "start_dt": now, "end_dt": now + timedelta(hours=1), "source": "classes"}
    base.update(kwargs)
    return CalendarEntry(**base)


def describe_CalendarEntry():
    def it_exposes_source_as_source_key():
        assert _entry(source="orientation").source_key == "orientation"

    def it_is_in_progress_between_start_and_end():
        now = timezone.now()
        assert _entry(start_dt=now - timedelta(hours=1), end_dt=now + timedelta(hours=1)).is_in_progress is True

    def it_is_not_in_progress_before_it_starts():
        now = timezone.now()
        assert _entry(start_dt=now + timedelta(hours=1), end_dt=now + timedelta(hours=2)).is_in_progress is False

    def it_is_never_in_progress_for_all_day_entries():
        now = timezone.now()
        entry = _entry(start_dt=now - timedelta(hours=1), end_dt=now + timedelta(hours=1), all_day=True)
        assert entry.is_in_progress is False


@pytest.mark.django_db
def describe_CalendarEntry_source_key():
    def it_keys_a_class_entry_by_its_guild():
        guild = GuildFactory()
        assert _entry(source="classes", guild=guild).source_key == str(guild.pk)

    def it_falls_back_to_classes_for_a_class_entry_with_no_guild():
        assert _entry(source="classes", guild=None).source_key == "classes"

    def it_keeps_orientation_its_own_color_even_with_a_guild():
        # Orientation stays amber — the guild must NOT recolor it.
        guild = GuildFactory()
        assert _entry(source="orientation", guild=guild).source_key == "orientation"

    def it_keeps_community_its_own_color_even_with_a_guild():
        # Community stays blue — the guild must NOT recolor it.
        guild = GuildFactory()
        assert _entry(source="community", guild=guild).source_key == "community"

    def it_prefers_a_stamped_feed_key_over_the_raw_source():
        assert _entry(source="community", feed_key="feed-7").source_key == "feed-7"

    def it_prefers_a_stamped_feed_key_even_when_a_guild_is_set():
        guild = GuildFactory()
        assert _entry(source="community", guild=guild, feed_key="feed-7").source_key == "feed-7"


@pytest.mark.django_db
def describe_google_target_feed_keys():
    def _configure(member_id: str = "", public_id: str = "") -> None:
        from core.models import SiteConfiguration

        config = SiteConfiguration.load()
        config.member_google_calendar_id = member_id
        config.public_google_calendar_id = public_id
        config.save()

    def it_maps_each_target_to_the_feed_embedding_its_calendar_id():
        from core.models import CalendarFeed

        _configure(member_id="member@group.calendar.google.com", public_id="public@group.calendar.google.com")
        # One feed embeds the id percent-encoded (Google's iCal URL shape), the other raw —
        # both forms must match.
        member_feed = CalendarFeed.objects.create(
            name="Member Calendar",
            ical_url="https://calendar.google.com/calendar/ical/member%40group.calendar.google.com/public/basic.ics",
            color="#eeb44b",
        )
        public_feed = CalendarFeed.objects.create(
            name="Public Calendar",
            ical_url="https://calendar.google.com/calendar/ical/public@group.calendar.google.com/public/basic.ics",
            color="#6fd880",
        )
        assert google_target_feed_keys() == {
            "member": f"feed-{member_feed.pk}",
            "public": f"feed-{public_feed.pk}",
        }

    def it_skips_a_target_whose_calendar_id_is_unset():
        from core.models import CalendarFeed

        _configure(public_id="public@group.calendar.google.com")
        feed = CalendarFeed.objects.create(
            name="Public Calendar",
            ical_url="https://calendar.google.com/calendar/ical/public%40group.calendar.google.com/public/basic.ics",
            color="#6fd880",
        )
        assert google_target_feed_keys() == {"public": f"feed-{feed.pk}"}

    def it_skips_a_target_with_no_matching_feed():
        from core.models import CalendarFeed

        _configure(member_id="member@group.calendar.google.com", public_id="public@group.calendar.google.com")
        CalendarFeed.objects.create(name="Unrelated", ical_url="https://example.com/other.ics", color="#888888")
        assert google_target_feed_keys() == {}

    def it_maps_both_targets_to_the_same_feed_when_they_share_a_calendar_id():
        # Degenerate config: both targets point at one Google calendar → both keys
        # resolve to that single feed's chip rather than one target being dropped.
        from core.models import CalendarFeed

        _configure(member_id="shared@group.calendar.google.com", public_id="shared@group.calendar.google.com")
        feed = CalendarFeed.objects.create(
            name="Shared Calendar",
            ical_url="https://calendar.google.com/calendar/ical/shared%40group.calendar.google.com/public/basic.ics",
            color="#6fd880",
        )
        assert google_target_feed_keys() == {"member": f"feed-{feed.pk}", "public": f"feed-{feed.pk}"}


def _guild_series(day_offsets: list[int]) -> object:
    """A guild with a published series whose sessions fall at the given day offsets."""
    guild = GuildFactory()
    category = CategoryFactory(guild=guild)
    offering = ClassOfferingFactory(
        status=ClassOffering.Status.PUBLISHED,
        category=category,
        scheduling_type=ClassOffering.SchedulingType.SERIES_PACKAGE,
    )
    for days in day_offsets:
        start = timezone.now() + timedelta(days=days)
        ClassSessionFactory(class_offering=offering, starts_at=start, ends_at=start + timedelta(hours=2))
    return guild


@pytest.mark.django_db
def describe_guild_calendar_entries():
    def _window() -> tuple[object, object]:
        today = timezone.now().date()
        return today - timedelta(days=10), today + timedelta(days=30)

    def it_omits_a_started_series():
        # A guild's own calendar must drop a started series just like the catalog.
        from hub.calendar_entries import guild_calendar_entries

        guild = _guild_series([-3, 4, 11])
        fetch_from, fetch_to = _window()

        entries = guild_calendar_entries(guild, fetch_from, fetch_to)

        assert [e for e in entries if e.source == "classes"] == []

    def it_keeps_a_future_series():
        from hub.calendar_entries import guild_calendar_entries

        guild = _guild_series([2, 9, 16])
        fetch_from, fetch_to = _window()

        entries = guild_calendar_entries(guild, fetch_from, fetch_to)

        class_entries = [e for e in entries if e.source == "classes"]
        assert len(class_entries) == 3


def describe_google_calendar_add_url():
    def it_builds_google_calendars_add_link_with_the_id_encoded():
        assert (
            google_calendar_add_url("abc123@group.calendar.google.com")
            == "https://calendar.google.com/calendar/r?cid=abc123%40group.calendar.google.com"
        )

    def it_is_blank_when_no_calendar_is_configured():
        assert google_calendar_add_url("") == ""


@pytest.mark.django_db
def describe_calendar_subscribe_links():
    def _configure(member: str, public: str) -> SiteConfiguration:
        config = SiteConfiguration.load()
        config.member_google_calendar_id = member
        config.public_google_calendar_id = public
        config.save()
        return config

    def it_gives_each_configured_calendar_both_link_forms():
        rows = calendar_subscribe_links(_configure("mem@group.calendar.google.com", "pub@group.calendar.google.com"))
        assert [row["key"] for row in rows] == ["member", "public"]
        assert rows[0] == {
            "key": "member",
            "label": "Member calendar",
            "webcal_url": google_calendar_subscribe_url("mem@group.calendar.google.com"),
            "google_url": google_calendar_add_url("mem@group.calendar.google.com"),
        }
        assert rows[1]["label"] == "Public calendar"

    def it_skips_a_calendar_with_no_id():
        assert [row["key"] for row in calendar_subscribe_links(_configure("", "pub@group.calendar.google.com"))] == [
            "public"
        ]
        assert calendar_subscribe_links(_configure("", "")) == []


@pytest.mark.django_db
def describe_google_calendar_event_url():
    def _params(url: str) -> dict[str, str]:
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://calendar.google.com/calendar/render"
        return {key: values[0] for key, values in parse_qs(parts.query).items()}

    def it_fills_in_one_event_in_the_makerspaces_local_time():
        pacific = ZoneInfo("America/Los_Angeles")
        event = CommunityEventFactory(
            community=True,
            title="Potluck & Pins",
            location="Common Area",
            description="Bring a dish.",
            starts_at=datetime(2026, 10, 10, 18, 0, tzinfo=pacific),
            ends_at=datetime(2026, 10, 10, 20, 30, tzinfo=pacific),
        )
        params = _params(google_calendar_event_url(event, event.starts_at, "https://pastlives.space/events/1/"))
        assert params == {
            "action": "TEMPLATE",
            "text": "Potluck & Pins",
            "dates": "20261010T180000/20261010T203000",
            "ctz": "America/Los_Angeles",
            "details": "Bring a dish.\n\nhttps://pastlives.space/events/1/",
            "location": "Common Area",
        }

    def it_lists_the_video_link_before_the_page_link_in_the_details():
        event = CommunityEventFactory(community=True, description="", video_url="https://meet.google.com/abc")
        params = _params(google_calendar_event_url(event, event.starts_at, "https://pastlives.space/events/1/"))
        assert params["details"] == "https://meet.google.com/abc\n\nhttps://pastlives.space/events/1/"

    def it_leaves_out_what_the_event_does_not_have():
        event = CommunityEventFactory(community=True, description="", location="", video_url="")
        params = _params(google_calendar_event_url(event, event.starts_at))
        assert set(params) == {"action", "text", "dates", "ctz"}

    def it_trims_a_long_description_so_the_link_stays_usable():
        event = CommunityEventFactory(community=True, description="x" * 5000)
        assert len(_params(google_calendar_event_url(event, event.starts_at))["details"]) == 3000

    def it_trims_by_encoded_length_so_emoji_cannot_overrun_it():
        event = CommunityEventFactory(community=True, description="\U0001f525" * 5000)
        details = _params(google_calendar_event_url(event, event.starts_at))["details"]
        assert details == "\U0001f525" * 250  # 12 encoded bytes each

    def it_carries_the_series_rule_on_the_local_weekday():
        # Monday 6 PM in Portland is Tuesday in UTC; the rule and the dates must both say Monday.
        event = CommunityEventFactory(
            community=True,
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            starts_at=datetime(2026, 10, 6, 1, 0, tzinfo=UTC),
            ends_at=datetime(2026, 10, 6, 3, 0, tzinfo=UTC),
        )
        params = _params(google_calendar_event_url(event, event.starts_at))
        assert params["recur"] == "RRULE:FREQ=WEEKLY;BYDAY=MO"
        assert params["dates"] == "20261005T180000/20261005T200000"

    def it_starts_a_series_on_the_date_given_and_keeps_its_length():
        # A member opened the November date; the series starts there, not on its first date.
        event = CommunityEventFactory(
            community=True,
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            starts_at=datetime(2026, 9, 2, 18, 0, tzinfo=ZoneInfo("America/Los_Angeles")),
            ends_at=datetime(2026, 9, 2, 20, 30, tzinfo=ZoneInfo("America/Los_Angeles")),
        )
        november = datetime(2026, 11, 4, 18, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        params = _params(google_calendar_event_url(event, november))
        assert params["dates"] == "20261104T180000/20261104T203000"
        assert params["recur"] == "RRULE:FREQ=MONTHLY;BYDAY=1WE"
