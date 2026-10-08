"""The Spotlight read model (#708, hub.spotlight): the meeting's next date, the lines, the poll."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from core.models import SiteConfiguration
from hub.spotlight import SECOND_LINE_DEFAULT, Spotlight
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory
from tests.polls.factories import poll_with

pytestmark = pytest.mark.django_db

PORTLAND = ZoneInfo("America/Los_Angeles")
NOW = datetime(2026, 10, 8, 9, 0, tzinfo=PORTLAND)


def _monthly_meeting(**kwargs: Any) -> CommunityEvent:
    """Second Tuesday, 6 to 7 PM, anchored a month back: Sep 8, Oct 13, Nov 10."""
    start = datetime(2026, 9, 8, 18, 0, tzinfo=PORTLAND)
    defaults: dict[str, Any] = {
        "community": True,
        "title": "Feature Request Meeting",
        "starts_at": start,
        "ends_at": start + timedelta(hours=1),
        "recurrence": CommunityEvent.Recurrence.MONTHLY,
        "video_url": "https://meet.example.org/feature-meeting",
    }
    defaults.update(kwargs)
    return CommunityEventFactory(**defaults)


def _build(now: datetime = NOW, **fields: Any) -> Spotlight:
    config = SiteConfiguration.load()
    for name, value in fields.items():
        setattr(config, name, value)
    config.save()
    return Spotlight.build(SiteConfiguration.load_with_spotlight_meeting(), now)


def describe_the_meeting():
    def it_is_absent_with_none_picked():
        assert _build().meeting is None

    def it_shows_a_repeating_meetings_next_date_with_its_links():
        event = _monthly_meeting()
        meeting = _build(spotlight_meeting_event=event).meeting

        assert meeting is not None
        assert meeting.starts_at == datetime(2026, 10, 13, 18, 0, tzinfo=PORTLAND)
        assert meeting.ics_url == f"/events/{event.pk}/event.ics?date=2026-10-13"
        assert meeting.detail_url == f"/events/{event.pk}/?date=2026-10-13"

    def it_keeps_a_meeting_that_is_under_way():
        ten_minutes_in = datetime(2026, 10, 13, 18, 10, tzinfo=PORTLAND)
        meeting = _build(now=ten_minutes_in, spotlight_meeting_event=_monthly_meeting()).meeting

        assert meeting is not None
        assert meeting.starts_at == datetime(2026, 10, 13, 18, 0, tzinfo=PORTLAND)

    def it_rolls_to_the_next_month_once_it_ends():
        at_the_end = datetime(2026, 10, 13, 19, 0, tzinfo=PORTLAND)
        meeting = _build(now=at_the_end, spotlight_meeting_event=_monthly_meeting()).meeting

        assert meeting is not None
        assert meeting.starts_at == datetime(2026, 11, 10, 18, 0, tzinfo=PORTLAND)

    def it_drops_a_one_off_that_has_ended():
        start = datetime(2026, 10, 1, 18, 0, tzinfo=PORTLAND)
        event = _monthly_meeting(starts_at=start, ends_at=start + timedelta(hours=1), recurrence="none")

        assert _build(spotlight_meeting_event=event).meeting is None

    def it_drops_an_unpublished_meeting():
        event = _monthly_meeting(moderation_state=CommunityEvent.ModerationState.PENDING)

        assert _build(spotlight_meeting_event=event).meeting is None

    def it_writes_the_date_line_and_the_pill():
        meeting = _build(spotlight_meeting_event=_monthly_meeting()).meeting

        assert meeting is not None
        assert meeting.when == "Tue, Oct 13 · 6:00 PM"
        assert meeting.pill == "Oct 13 @ 6 PM"

    def it_keeps_the_minutes_in_the_pill_when_there_are_some():
        start = datetime(2026, 9, 8, 18, 30, tzinfo=PORTLAND)
        meeting = _build(
            spotlight_meeting_event=_monthly_meeting(starts_at=start, ends_at=start + timedelta(hours=1))
        ).meeting

        assert meeting is not None
        assert meeting.pill == "Oct 13 @ 6:30 PM"

    def it_forgets_the_pick_when_the_event_is_deleted():
        event = _monthly_meeting()
        _build(spotlight_meeting_event=event)

        event.delete()

        assert SiteConfiguration.load().spotlight_meeting_event is None


def describe_the_lines():
    def it_uses_the_admins_lines_when_set():
        poll_with("Laser", "Lathe", question="Zorblax next?", opens_at=NOW - timedelta(hours=1))
        spotlight = _build(spotlight_first_line="Vote on the shop", spotlight_second_line="Come say hi")

        assert spotlight.first_line == "Vote on the shop"
        assert spotlight.second_line == "Come say hi"

    def it_falls_back_to_the_poll_question_and_the_meeting_name():
        poll_with("Laser", "Lathe", question="Zorblax next?", opens_at=NOW - timedelta(hours=1))
        spotlight = _build()

        assert spotlight.first_line == "Zorblax next?"
        assert spotlight.second_line == SECOND_LINE_DEFAULT == "Feature Request Meeting"

    def it_leaves_the_first_line_empty_with_no_text_and_no_poll():
        assert _build().first_line == ""


def describe_the_poll():
    def it_carries_the_open_poll_and_its_tally():
        poll = poll_with("Laser", "Lathe", votes=(2, 1), opens_at=NOW - timedelta(hours=1))
        spotlight = _build()

        assert spotlight.poll == poll
        assert [(result.text, result.votes) for result in spotlight.results] == [("Laser", 2), ("Lathe", 1)]
        assert spotlight.total_votes == 3

    def it_has_no_poll_when_none_is_open():
        poll_with("Laser", "Lathe", opens_at=NOW - timedelta(days=9), closes_at=NOW - timedelta(days=2))

        spotlight = _build()

        assert spotlight.poll is None
        assert spotlight.results == []
