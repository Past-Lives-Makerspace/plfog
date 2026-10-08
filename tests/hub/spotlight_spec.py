"""The Spotlight read model (#708, hub.spotlight): the meeting's next date, the lines, the poll."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from core.models import SiteConfiguration
from hub.spotlight import SECOND_LINE_DEFAULT, Spotlight
from membership.models import CommunityEvent, Member
from tests.membership.factories import CommunityEventFactory, MemberFactory
from tests.polls.factories import PollVoteFactory, poll_with

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
    return Spotlight.load(None, now)


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

    def it_falls_back_to_the_latest_update_with_no_text_and_no_poll(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "plfog.version.CHANGELOG", [{"title": "Zorblax update", "date": "2026-10-07", "changes": [], "slug": "zx"}]
        )

        assert _build().first_line == "Zorblax update"

    def it_is_empty_with_no_text_no_poll_and_no_changelog(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [])

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


def describe_load():
    def it_reads_settings_meeting_poll_answers_and_vote_in_one_query():
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        poll = poll_with("Laser", "Lathe", "Kiln", votes=(2, 1, 0), opens_at=NOW - timedelta(hours=1))
        member = MemberFactory()
        PollVoteFactory(choice=poll.choices.get(text="Kiln"), member=member)
        config = SiteConfiguration.load()
        config.spotlight_meeting_event = _monthly_meeting()
        config.save()

        with CaptureQueriesContext(connection) as queries:
            spotlight = Spotlight.load(member, NOW)
            meeting_title = spotlight.meeting.title if spotlight.meeting else ""

        assert len(queries) == 1
        assert meeting_title == "Feature Request Meeting"
        assert spotlight.poll is not None and spotlight.poll.pk == poll.pk
        assert [(r.text, r.votes) for r in spotlight.results] == [("Laser", 2), ("Lathe", 1), ("Kiln", 1)]
        assert spotlight.my_choice_pk == poll.choices.get(text="Kiln").pk

    def it_reads_six_answers_in_order():
        answers = [f"Answer {n}" for n in range(6)]
        poll_with(*answers, opens_at=NOW - timedelta(hours=1))

        assert [r.text for r in _build().results] == answers

    def it_leaves_the_vote_unmarked_for_a_member_who_has_not_voted():
        poll_with("Laser", "Lathe", opens_at=NOW - timedelta(hours=1))

        spotlight = Spotlight.load(MemberFactory(), NOW)

        assert spotlight.my_choice_pk is None
        assert spotlight.card is not None and spotlight.card.shows_choices is True

    def it_shows_a_voters_card_as_results():
        poll = poll_with("Laser", "Lathe", opens_at=NOW - timedelta(hours=1))
        member = MemberFactory()
        PollVoteFactory(choice=poll.choices.first(), member=member)

        card = Spotlight.load(member, NOW).card

        assert card is not None and card.shows_choices is False

    def it_has_no_card_without_an_open_poll():
        assert Spotlight.load(MemberFactory(), NOW).card is None

    def it_never_lets_a_guest_vote_from_it():
        poll_with("Laser", "Lathe", opens_at=NOW - timedelta(hours=1))

        card = Spotlight.load(MemberFactory(status=Member.Status.GUEST), NOW).card

        assert card is not None and card.shows_choices is False


def describe_seen_signature():
    def it_is_empty_parts_with_nothing_to_show():
        assert _build().seen_signature == "||"

    def it_names_the_poll_the_next_meeting_and_the_text_stamp():
        poll = poll_with("Laser", "Lathe", opens_at=NOW - timedelta(hours=1))
        stamp = datetime(2026, 10, 7, 12, 0, tzinfo=PORTLAND)

        signature = _build(spotlight_meeting_event=_monthly_meeting(), spotlight_text_changed_at=stamp).seen_signature

        assert signature == f"{poll.pk}|2026-10-14T01:00:00+00:00|2026-10-07T19:00:00+00:00"

    def it_changes_when_the_monthly_meeting_rolls_on():
        event = _monthly_meeting()
        before = _build(spotlight_meeting_event=event).seen_signature

        after = _build(now=datetime(2026, 10, 13, 19, 0, tzinfo=PORTLAND), spotlight_meeting_event=event).seen_signature

        assert before != after

    def it_changes_when_a_new_poll_opens():
        before = _build().seen_signature
        poll_with("Laser", "Lathe", opens_at=NOW - timedelta(hours=1))

        assert _build().seen_signature != before

    def it_changes_when_the_text_changes():
        before = _build().seen_signature

        assert _build(spotlight_text_changed_at=NOW).seen_signature != before


ENTRY = {"title": "Zorblax update", "date": "2026-10-07", "changes": [], "slug": "701-zorblax"}


def describe_no_open_poll():
    def it_offers_the_newest_update_with_its_anchor(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [ENTRY, {**ENTRY, "title": "Older", "slug": "older"}])

        update = _build(spotlight_meeting_event=_monthly_meeting()).latest_update

        assert update is not None
        assert (update.title, update.date, update.anchor) == ("Zorblax update", "2026-10-07", "changelog-701-zorblax")

    def it_gives_a_swept_entry_no_anchor(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "plfog.version.CHANGELOG", [{"version": "2.60.0", "title": "Old", "date": "2026-09-30", "changes": []}]
        )

        update = _build().latest_update

        assert update is not None and update.anchor == ""

    def it_still_shows_with_a_meeting_even_with_the_toggle_off():
        spotlight = _build(spotlight_meeting_event=_monthly_meeting(), spotlight_show_when_empty=False)

        assert spotlight.is_empty is False
        assert bool(spotlight) is True

    def it_still_shows_with_a_poll_even_with_the_toggle_off():
        poll_with("Laser", "Lathe", opens_at=NOW - timedelta(hours=1))

        assert bool(_build(spotlight_show_when_empty=False)) is True


def describe_no_poll_and_no_meeting():
    def it_shows_while_the_toggle_is_on():
        spotlight = _build(spotlight_show_when_empty=True)

        assert spotlight.is_empty is True
        assert bool(spotlight) is True

    def it_is_gone_while_the_toggle_is_off():
        assert bool(_build(spotlight_show_when_empty=False)) is False

    def it_defaults_to_on():
        assert SiteConfiguration.load().spotlight_show_when_empty is True


def describe_the_dot_and_releases():
    def it_never_changes_the_signature_for_a_new_release(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [ENTRY])
        before = _build().seen_signature

        monkeypatch.setattr("plfog.version.CHANGELOG", [{**ENTRY, "title": "Newer", "slug": "702-newer"}, ENTRY])

        assert _build().seen_signature == before
