"""The Spotlight read model (#708, hub.spotlight): the meeting's next date, the lines, the announcements (#890)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from core.factories import AnnouncementFactory
from core.models import Announcement, SiteConfiguration
from hub.spotlight import SECOND_LINE_DEFAULT, Spotlight, sees_spotlight
from membership.models import CommunityEvent, Member
from tests.membership.factories import CommunityEventFactory, MemberFactory

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
    return Spotlight.load(now)


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
        AnnouncementFactory(body="<p>Zorblax notice</p>")
        spotlight = _build(spotlight_first_line="Come to the shop", spotlight_second_line="Come say hi")

        assert spotlight.first_line == "Come to the shop"
        assert spotlight.second_line == "Come say hi"

    def it_falls_back_to_the_first_announcement_on_one_line_and_the_meeting_name():
        AnnouncementFactory(body="<p>Zorblax second</p>", position=1)
        AnnouncementFactory(body="<p>Zorblax <strong>first</strong></p><p>and   more</p>", position=0)

        spotlight = _build()

        assert spotlight.first_line == "Zorblax first and more"
        assert spotlight.second_line == SECOND_LINE_DEFAULT == "Feature Request Meeting"

    def it_falls_back_to_the_latest_update_with_no_text_and_no_announcement(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "plfog.version.CHANGELOG", [{"title": "Zorblax update", "date": "2026-10-07", "changes": [], "slug": "zx"}]
        )

        assert _build().first_line == "Zorblax update"

    def it_skips_an_announcement_that_is_not_live(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "plfog.version.CHANGELOG", [{"title": "Zorblax update", "date": "2026-10-07", "changes": [], "slug": "zx"}]
        )
        AnnouncementFactory(is_active=False)

        assert _build().first_line == "Zorblax update"

    def it_is_empty_with_no_text_no_announcement_and_no_changelog(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [])

        assert _build().first_line == ""


def describe_the_announcements():
    def it_carries_the_live_ones_in_the_admins_order():
        second = AnnouncementFactory(position=1)
        first = AnnouncementFactory(position=0)
        AnnouncementFactory(is_active=False, position=0)
        AnnouncementFactory(show_from=NOW + timedelta(hours=1))
        AnnouncementFactory(show_until=NOW - timedelta(hours=1))

        assert _build().announcements == [first, second]

    def it_has_none_with_nothing_live():
        assert _build().announcements == []

    def it_counts_as_something_to_show_even_with_the_toggle_off():
        AnnouncementFactory()

        spotlight = _build(spotlight_show_when_empty=False)

        assert spotlight.is_empty is False
        assert bool(spotlight) is True


def describe_without_announcements():
    """Home's phone copy (#890): the banner just above already shows the announcements."""

    def it_leaves_them_out_and_keeps_everything_else():
        AnnouncementFactory(body="<p>Zorblax notice</p>")
        spotlight = _build(spotlight_meeting_event=_monthly_meeting())

        home = spotlight.without_announcements

        assert home.shown_announcements == []
        assert home.announcements == spotlight.announcements
        assert home.meeting == spotlight.meeting
        assert spotlight.shown_announcements == spotlight.announcements

    def it_is_empty_with_only_announcements(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "plfog.version.CHANGELOG", [{"title": "Zorblax update", "date": "2026-10-07", "changes": [], "slug": "zx"}]
        )
        AnnouncementFactory(body="<p>Zorblax notice</p>")

        home = _build(spotlight_show_when_empty=False).without_announcements

        assert home.is_empty is True
        assert bool(home) is False
        assert home.first_line == "Zorblax update"

    def it_keeps_the_seen_signature_so_opening_it_marks_them_seen():
        AnnouncementFactory()
        spotlight = _build()

        assert spotlight.without_announcements.seen_signature == spotlight.seen_signature


def describe_load():
    def it_reads_the_settings_the_meeting_and_the_announcements_in_two_queries():
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        AnnouncementFactory(position=0)
        AnnouncementFactory(position=1)
        AnnouncementFactory(position=2)
        config = SiteConfiguration.load()
        config.spotlight_meeting_event = _monthly_meeting()
        config.save()

        with CaptureQueriesContext(connection) as queries:
            spotlight = Spotlight.load(NOW)
            meeting_title = spotlight.meeting.title if spotlight.meeting else ""
            texts = [announcement.text for announcement in spotlight.announcements]

        assert len(queries) == 2
        assert meeting_title == "Feature Request Meeting"
        assert len(texts) == 3

    def it_makes_the_settings_row_on_a_fresh_install():
        SiteConfiguration.objects.all().delete()

        spotlight = Spotlight.load(NOW)

        assert SiteConfiguration.objects.filter(pk=1).exists()
        assert spotlight.show_when_empty is True


def describe_seen_signature():
    def it_is_empty_parts_with_nothing_to_show():
        assert _build().seen_signature == "||"

    def it_names_the_announcements_the_next_meeting_and_the_text_stamp():
        announcement = AnnouncementFactory()
        stamp = datetime(2026, 10, 7, 12, 0, tzinfo=PORTLAND)

        signature = _build(spotlight_meeting_event=_monthly_meeting(), spotlight_text_changed_at=stamp).seen_signature

        stamp_of = f"{announcement.pk}:{announcement.updated_at.timestamp():.6f}"
        assert signature == f"{stamp_of}|2026-10-14T01:00:00+00:00|2026-10-07T19:00:00+00:00"

    def it_signs_the_announcements_as_the_home_banner_does():
        rows = [AnnouncementFactory(position=1), AnnouncementFactory(position=0)]

        assert _build().seen_signature == f"{Announcement.signature(rows)}||"

    def it_changes_when_the_monthly_meeting_rolls_on():
        event = _monthly_meeting()
        before = _build(spotlight_meeting_event=event).seen_signature

        after = _build(now=datetime(2026, 10, 13, 19, 0, tzinfo=PORTLAND), spotlight_meeting_event=event).seen_signature

        assert before != after

    def it_changes_when_an_announcement_is_added():
        AnnouncementFactory()
        before = _build().seen_signature

        AnnouncementFactory()

        assert _build().seen_signature != before

    def it_changes_when_an_announcement_is_edited():
        announcement = AnnouncementFactory(body="<p>Zorblax one</p>")
        before = _build().seen_signature

        announcement.body = "<p>Zorblax edited</p>"
        announcement.save()

        assert _build().seen_signature != before

    def it_keeps_the_signature_when_only_the_order_changes():
        first = AnnouncementFactory(position=0)
        second = AnnouncementFactory(position=1)
        before = _build().seen_signature

        Announcement.objects.filter(pk=first.pk).update(position=1)
        Announcement.objects.filter(pk=second.pk).update(position=0)

        assert _build().seen_signature == before

    def it_changes_when_the_text_changes():
        before = _build().seen_signature

        assert _build(spotlight_text_changed_at=NOW).seen_signature != before


ENTRY = {"title": "Zorblax update", "date": "2026-10-07", "changes": [], "slug": "701-zorblax"}


def describe_the_latest_update():
    def it_offers_the_newest_update_with_its_anchor(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [ENTRY, {**ENTRY, "title": "Older", "slug": "older"}])

        update = _build(spotlight_meeting_event=_monthly_meeting()).latest_update

        assert update is not None
        assert (update.title, update.date, update.anchor) == ("Zorblax update", "2026-10-07", "changelog-701-zorblax")
        assert update.short_date == "Oct 7"

    def it_gives_a_swept_entry_no_anchor(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(
            "plfog.version.CHANGELOG", [{"version": "2.60.0", "title": "Old", "date": "2026-09-30", "changes": []}]
        )

        update = _build().latest_update

        assert update is not None and update.anchor == ""

    def it_has_none_with_an_empty_changelog(monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("plfog.version.CHANGELOG", [])

        assert _build().latest_update is None

    def it_still_shows_with_a_meeting_even_with_the_toggle_off():
        spotlight = _build(spotlight_meeting_event=_monthly_meeting(), spotlight_show_when_empty=False)

        assert spotlight.is_empty is False
        assert bool(spotlight) is True


def describe_no_announcement_and_no_meeting():
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


def describe_sees_spotlight():
    """Who sees the Spotlight: the polls app's can_vote rule (#709), moved here unchanged in #890."""

    @pytest.mark.parametrize(
        ("status", "allowed"),
        [
            (Member.Status.ACTIVE, True),
            (Member.Status.SUSPENDED, True),
            (Member.Status.GUEST, False),
            (Member.Status.FORMER, False),
        ],
    )
    def it_lets_members_in_but_not_guests_or_former_members(status: str, allowed: bool):
        assert sees_spotlight(MemberFactory(status=status)) is allowed

    def it_leaves_out_an_account_with_no_member():
        assert sees_spotlight(None) is False
