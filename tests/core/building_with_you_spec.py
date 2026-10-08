"""The "Building with you" panel's data and the pill's news rule (#699, core.building_with_you)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from core.building_with_you import BuildingWithYou, ShippedEntry
from core.models import SiteConfiguration
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory

pytestmark = pytest.mark.django_db

PORTLAND = ZoneInfo("America/Los_Angeles")
# A quiet Thursday morning, Portland time; the factory's events and changelog dates hang off it.
NOW = datetime(2026, 10, 8, 9, 0, tzinfo=PORTLAND)
OLD_CHANGELOG: list[dict[str, Any]] = [
    {"title": "Kiln tickets", "date": "2026-10-01", "changes": []},
    {"title": "Feedback requests", "date": "2026-09-28", "changes": []},
    {"title": "Locations", "date": "2026-09-20", "changes": []},
    {"title": "Guest accounts", "date": "2026-09-10", "changes": []},
]


def _config(**fields: Any) -> SiteConfiguration:
    config = SiteConfiguration.load()
    for name, value in fields.items():
        setattr(config, name, value)
    config.save()
    return SiteConfiguration.load_with_feature_meeting()


def _monthly_meeting(**kwargs: Any) -> CommunityEvent:
    """Second Tuesday at 6 PM, anchored a month back: Sep 8, Oct 13, Nov 10."""
    start = datetime(2026, 9, 8, 18, 0, tzinfo=PORTLAND)
    defaults: dict[str, Any] = {
        "community": True,
        "title": "Member Portal Feature Meeting",
        "starts_at": start,
        "ends_at": start + timedelta(hours=1),
        "recurrence": CommunityEvent.Recurrence.MONTHLY,
        "video_url": "https://meet.example.org/feature-meeting",
    }
    defaults.update(kwargs)
    return CommunityEventFactory(**defaults)


def _build(now: datetime = NOW, changelog: list[dict[str, Any]] | None = None, **fields: Any) -> BuildingWithYou:
    return BuildingWithYou.build(_config(**fields), now, OLD_CHANGELOG if changelog is None else changelog)


def describe_the_next_feature_meeting():
    def it_is_absent_with_no_meeting_picked():
        assert _build().meeting is None

    def it_shows_a_repeating_meetings_next_occurrence_not_its_first_date():
        meeting = _build(feature_meeting_event=_monthly_meeting()).meeting

        assert meeting is not None
        assert meeting.title == "Member Portal Feature Meeting"
        assert meeting.starts_at == datetime(2026, 10, 13, 18, 0, tzinfo=PORTLAND)

    def it_links_a_repeating_meetings_calendar_file_and_page_to_that_date():
        event = _monthly_meeting()
        meeting = _build(feature_meeting_event=event).meeting

        assert meeting is not None
        assert meeting.ics_url == f"/events/{event.pk}/event.ics?date=2026-10-13"
        assert meeting.detail_url == f"/events/{event.pk}/?date=2026-10-13"
        assert meeting.video_url == "https://meet.example.org/feature-meeting"

    def it_skips_a_meeting_that_already_started_today():
        during = datetime(2026, 10, 13, 18, 30, tzinfo=PORTLAND)
        meeting = _build(now=during, feature_meeting_event=_monthly_meeting()).meeting

        assert meeting is not None
        assert meeting.starts_at == datetime(2026, 11, 10, 18, 0, tzinfo=PORTLAND)

    def it_shows_a_future_one_off_meeting_with_plain_links():
        start = datetime(2026, 10, 20, 18, 0, tzinfo=PORTLAND)
        event = _monthly_meeting(starts_at=start, ends_at=start + timedelta(hours=1), recurrence="none")
        meeting = _build(feature_meeting_event=event).meeting

        assert meeting is not None
        assert meeting.starts_at == start
        assert meeting.ics_url == f"/events/{event.pk}/event.ics"
        assert meeting.detail_url == f"/events/{event.pk}/"

    def it_is_absent_when_a_one_off_meeting_is_over():
        start = datetime(2026, 10, 1, 18, 0, tzinfo=PORTLAND)
        event = _monthly_meeting(starts_at=start, ends_at=start + timedelta(hours=1), recurrence="none")

        assert _build(feature_meeting_event=event).meeting is None

    def it_is_absent_when_the_meeting_is_not_published():
        event = _monthly_meeting(moderation_state=CommunityEvent.ModerationState.PENDING)

        assert _build(feature_meeting_event=event).meeting is None

    def it_keeps_a_blank_join_link_blank():
        meeting = _build(feature_meeting_event=_monthly_meeting(video_url="")).meeting

        assert meeting is not None
        assert meeting.video_url == ""

    def it_forgets_the_pick_when_the_event_is_deleted():
        event = _monthly_meeting()
        _config(feature_meeting_event=event)

        event.delete()

        assert SiteConfiguration.load().feature_meeting_event is None


def describe_being_built_now():
    def it_lists_each_non_blank_line_trimmed():
        built = _build(being_built_now="  Kiln tickets for everyone \n\n   \nBook a laser by the hour\n").being_built

        assert built == ["Kiln tickets for everyone", "Book a laser by the hour"]

    def it_is_empty_when_the_setting_is():
        assert _build(being_built_now="").being_built == []


def describe_recently_shipped():
    def it_is_the_three_newest_entries_with_their_dates():
        shipped = _build().recently_shipped

        assert shipped == [
            ShippedEntry(title="Kiln tickets", shipped_on=date(2026, 10, 1)),
            ShippedEntry(title="Feedback requests", shipped_on=date(2026, 9, 28)),
            ShippedEntry(title="Locations", shipped_on=date(2026, 9, 20)),
        ]


def describe_the_backlog_link():
    def it_defaults_to_the_github_project():
        assert SiteConfiguration.load().backlog_url == "https://github.com/orgs/Past-Lives-Makerspace/projects/1"

    def it_is_blank_when_cleared():
        assert _build(backlog_url="").backlog_url == ""


def describe_is_loud():
    def it_is_quiet_with_no_meeting_and_no_fresh_release():
        assert _build().is_loud is False

    def it_is_loud_when_the_meeting_starts_exactly_72_hours_out():
        before = datetime(2026, 10, 10, 18, 0, tzinfo=PORTLAND)

        assert _build(now=before, feature_meeting_event=_monthly_meeting()).is_loud is True

    def it_is_quiet_a_minute_before_the_72_hours_begin():
        before = datetime(2026, 10, 10, 17, 59, tzinfo=PORTLAND)

        assert _build(now=before, feature_meeting_event=_monthly_meeting()).is_loud is False

    def it_is_loud_when_a_release_is_dated_today():
        fresh = [{"title": "Today", "date": "2026-10-08", "changes": []}, *OLD_CHANGELOG]

        assert _build(changelog=fresh).is_loud is True

    def it_is_loud_when_a_release_is_dated_yesterday():
        fresh = [{"title": "Yesterday", "date": "2026-10-07", "changes": []}, *OLD_CHANGELOG]

        assert _build(changelog=fresh).is_loud is True

    def it_is_quiet_when_the_newest_release_is_two_days_old():
        stale = [{"title": "Tuesday", "date": "2026-10-06", "changes": []}, *OLD_CHANGELOG]

        assert _build(changelog=stale).is_loud is False

    def it_judges_today_in_portland_not_utc():
        # 11:30 PM Thursday in Portland is already Friday in UTC, when Wednesday's release
        # would be two days old; in Portland it is still yesterday's.
        late = datetime(2026, 10, 8, 23, 30, tzinfo=PORTLAND)
        wednesday = [{"title": "Wednesday", "date": "2026-10-07", "changes": []}]

        assert _build(now=late, changelog=wednesday).is_loud is True

    def it_is_quiet_with_an_empty_changelog():
        assert _build(changelog=[]).is_loud is False


def describe_SiteConfiguration_load_with_feature_meeting():
    def it_reads_the_settings_and_the_meeting_in_one_query():
        _config(feature_meeting_event=_monthly_meeting())

        with CaptureQueriesContext(connection) as queries:
            config = SiteConfiguration.load_with_feature_meeting()
            title = config.feature_meeting_event.title

        assert title == "Member Portal Feature Meeting"
        assert len(queries) == 1

    def it_builds_the_panel_from_the_live_changelog():
        from plfog.version import CHANGELOG

        panel = SiteConfiguration.load_with_feature_meeting().building_with_you(NOW)

        assert [entry.title for entry in panel.recently_shipped] == [entry["title"] for entry in CHANGELOG[:3]]
