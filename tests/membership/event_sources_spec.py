"""BDD specs for the community-event scheduler sources: reminder + happening-now
occurrence yield, the 15-minute due-window math, per-offset and per-date dedupe, the
past-offset skip, and a repeating series reminding before every date."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.utils import timezone
from factory.django import mute_signals

from core.events.scheduler import run_due, run_sources
from core.models import Notification
from membership.events import event_happening_now_occurrences, event_reminder_occurrences
from membership.models import CommunityEvent
from tests.membership.factories import (
    CommunityEventFactory,
    GuildFactory,
    GuildMembershipFactory,
    MemberFactory,
)

pytestmark = pytest.mark.django_db


def _guild_member(guild) -> User:
    """An active guild member with a linked, email-bearing User (an addressable recipient)."""
    member = MemberFactory()
    with mute_signals(post_save):
        user = User.objects.create_user(username=f"gm_{member.pk}", email=f"gm_{member.pk}@example.com")
    member.user = user
    member.save(update_fields=["user"])
    GuildMembershipFactory(guild=guild, member=member)
    return user


def _date(event: CommunityEvent) -> str:
    return timezone.localtime(event.starts_at).date().isoformat()


def describe_event_reminder_occurrences():
    def it_yields_each_enabled_offset_at_its_own_send_time_with_a_dated_period():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        event = CommunityEventFactory(starts_at=now + timedelta(days=7), remind_7d=True, remind_1d=True)
        week_out = {o.period for o in event_reminder_occurrences(now)}
        day_out = {o.period for o in event_reminder_occurrences(now + timedelta(days=6))}
        assert week_out == {f"event:{event.pk}:reminder:7d:{_date(event)}"}
        assert day_out == {f"event:{event.pk}:reminder:1d:{_date(event)}"}

    def it_marks_due_only_the_offset_whose_fire_time_lands_in_the_tick():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        event = CommunityEventFactory(starts_at=now + timedelta(days=7), remind_7d=True, remind_3d=True, remind_1d=True)
        due = [o for o in event_reminder_occurrences(now) if o.is_due(now=now)]
        assert [o.period for o in due] == [f"event:{event.pk}:reminder:7d:{_date(event)}"]

    def it_never_yields_an_offset_whose_time_has_passed():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        CommunityEventFactory(starts_at=now + timedelta(hours=12), remind_7d=True, remind_3d=True)
        occurrences = list(event_reminder_occurrences(now))
        assert occurrences == []
        assert run_due(occurrences, now=now) == 0

    def it_excludes_unpublished_events():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        CommunityEventFactory(pending=True, starts_at=now + timedelta(days=7), remind_7d=True)
        assert list(event_reminder_occurrences(now)) == []

    def it_delivers_once_then_dedupes_a_second_tick_in_the_same_window():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        guild = GuildFactory()
        member = _guild_member(guild)
        CommunityEventFactory(guild=guild, starts_at=now + timedelta(days=7), remind_7d=True)

        first = run_sources([event_reminder_occurrences], now=now)
        second = run_sources([event_reminder_occurrences], now=now)
        assert first == 1
        assert second == 0  # deduped on EventDelivery period event:{pk}:reminder:7d:{date}
        assert Notification.objects.filter(trigger="event.reminder", user=member).count() == 1

    def it_reminds_before_each_date_of_a_series_whose_first_date_has_passed():
        # v1 anchored on starts_at, so a monthly series stopped reminding after its first date
        # while the editor still let a lead turn reminders on for it.
        first = timezone.make_aware(datetime(2026, 6, 11, 18, 0))  # the 2nd Thursday
        event = CommunityEventFactory(
            starts_at=first,
            ends_at=first + timedelta(hours=2),
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            remind_7d=True,
        )
        july, august = event.occurrences_in(datetime(2026, 7, 1).date(), datetime(2026, 8, 31).date())
        for date_start in (july, august):
            send = date_start - timedelta(days=7)
            (occurrence,) = list(event_reminder_occurrences(send))
            assert occurrence.is_due(now=send)
            assert occurrence.period == f"event:{event.pk}:reminder:7d:{date_start.date().isoformat()}"
            assert occurrence.context["when"].startswith(date_start.strftime("%a, %b %-d"))

    def it_sends_each_date_of_a_series_once():
        guild = GuildFactory()
        member = _guild_member(guild)
        first = timezone.make_aware(datetime(2026, 6, 4, 18, 0))
        event = CommunityEventFactory(
            guild=guild,
            starts_at=first,
            ends_at=first + timedelta(hours=2),
            recurrence=CommunityEvent.Recurrence.WEEKLY,
            remind_1d=True,
        )
        dates = event.occurrences_in(datetime(2026, 7, 1).date(), datetime(2026, 7, 16).date())
        assert len(dates) == 3
        for date_start in dates:
            send = date_start - timedelta(days=1)
            assert run_sources([event_reminder_occurrences], now=send) == 1
            assert run_sources([event_reminder_occurrences], now=send) == 0
        assert Notification.objects.filter(trigger="event.reminder", user=member).count() == 3

    def it_sends_nothing_for_studio_hours_even_with_a_toggle_on():
        # Studio hours are weekly rows, exactly the shape a series now reminds on.
        first = timezone.make_aware(datetime(2026, 6, 4, 18, 0))
        event = CommunityEventFactory(
            studio_hours=True, starts_at=first, ends_at=first + timedelta(hours=3), remind_1d=True
        )
        (date_start,) = event.occurrences_in(datetime(2026, 7, 2).date(), datetime(2026, 7, 2).date())
        assert list(event_reminder_occurrences(date_start - timedelta(days=1))) == []

    def it_fires_for_a_future_first_start_of_a_recurring_series():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        event = CommunityEventFactory(
            starts_at=now + timedelta(days=7),
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            remind_7d=True,
        )
        due = [o for o in event_reminder_occurrences(now) if o.is_due(now=now)]
        assert [o.period for o in due] == [f"event:{event.pk}:reminder:7d:{_date(event)}"]


def describe_event_happening_now_occurrences():
    def it_only_yields_events_that_opted_in():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        opted_in = CommunityEventFactory(starts_at=now + timedelta(minutes=5), notify_happening_now=True)
        CommunityEventFactory(starts_at=now + timedelta(minutes=5), notify_happening_now=False)
        periods = {o.period for o in event_happening_now_occurrences(now)}
        assert periods == {f"event:{opted_in.pk}:happening_now:{_date(opted_in)}"}

    def it_uses_a_zero_offset_anchored_on_the_start():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        event = CommunityEventFactory(starts_at=now + timedelta(minutes=5), notify_happening_now=True)
        occurrence = next(iter(event_happening_now_occurrences(now)))
        assert occurrence.offset == timedelta(0)
        assert occurrence.anchor == event.starts_at

    def it_excludes_a_start_beyond_the_tick_window():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        CommunityEventFactory(starts_at=now + timedelta(hours=2), notify_happening_now=True)
        assert list(event_happening_now_occurrences(now)) == []

    def it_pings_as_each_date_of_a_series_begins():
        first = timezone.make_aware(datetime(2026, 6, 11, 18, 0))
        event = CommunityEventFactory(
            starts_at=first,
            ends_at=first + timedelta(hours=2),
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            notify_happening_now=True,
        )
        (july,) = event.occurrences_in(datetime(2026, 7, 1).date(), datetime(2026, 7, 31).date())
        (occurrence,) = list(event_happening_now_occurrences(july - timedelta(minutes=5)))
        assert occurrence.anchor == july
        assert occurrence.period == f"event:{event.pk}:happening_now:{july.date().isoformat()}"

    def it_pings_nothing_for_studio_hours_even_with_the_toggle_on():
        first = timezone.make_aware(datetime(2026, 6, 4, 18, 0))
        event = CommunityEventFactory(
            studio_hours=True, starts_at=first, ends_at=first + timedelta(hours=3), notify_happening_now=True
        )
        (date_start,) = event.occurrences_in(datetime(2026, 7, 2).date(), datetime(2026, 7, 2).date())
        assert list(event_happening_now_occurrences(date_start - timedelta(minutes=5))) == []

    def it_delivers_once_then_dedupes():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        guild = GuildFactory()
        member = _guild_member(guild)
        CommunityEventFactory(guild=guild, starts_at=now + timedelta(minutes=5), notify_happening_now=True)

        first = run_sources([event_happening_now_occurrences], now=now)
        second = run_sources([event_happening_now_occurrences], now=now)
        assert first == 1
        assert second == 0
        assert Notification.objects.filter(trigger="event.happening_now", user=member).count() == 1


def describe_reminder_join_url():
    """The Meetings §6.6 rail: a linked meeting's video-call link rides the reminder context."""

    def _reminder_context(event: CommunityEvent) -> dict:
        now = event.starts_at - timedelta(days=3)  # the 3-day reminder's own send time
        occurrence = next(iter(event_reminder_occurrences(now)))
        return occurrence.context

    def it_carries_the_linked_meetings_video_call_url():
        from tests.membership.factories import MeetingFactory

        event = CommunityEventFactory(remind_3d=True)
        occurrence_date = timezone.localtime(event.starts_at).date()
        MeetingFactory(
            guild=event.guild,
            scheduled_date=occurrence_date,
            event=event,
            event_occurrence=occurrence_date,
            video_call_url="https://meet.example/abc-defg",
        )
        context = _reminder_context(event)
        assert context["join_url"] == "https://meet.example/abc-defg"
        assert 'href="https://meet.example/abc-defg"' in context["join_cta"]
        assert "Join meeting" in context["join_cta"]
        assert context["join_line"] == "Join the meeting: https://meet.example/abc-defg\n"

    def it_is_none_with_no_linked_meeting():
        event = CommunityEventFactory(remind_3d=True)
        context = _reminder_context(event)
        assert context["join_url"] is None
        # The copy always substitutes these two, so they must exist — empty, not [missing:].
        assert context["join_cta"] == ""
        assert context["join_line"] == ""

    def it_is_none_when_the_linked_meeting_has_no_video_url():
        from tests.membership.factories import MeetingFactory

        event = CommunityEventFactory(remind_3d=True)
        occurrence_date = timezone.localtime(event.starts_at).date()
        MeetingFactory(
            guild=event.guild,
            scheduled_date=occurrence_date,
            event=event,
            event_occurrence=occurrence_date,
            video_call_url="",
        )
        context = _reminder_context(event)
        assert context["join_url"] is None

    def it_ignores_a_meeting_pinned_to_a_different_occurrence():
        from tests.membership.factories import MeetingFactory

        event = CommunityEventFactory(remind_3d=True)
        other_date = timezone.localtime(event.starts_at).date() + timedelta(days=28)
        MeetingFactory(
            guild=event.guild,
            scheduled_date=other_date,
            event=event,
            event_occurrence=other_date,
            video_call_url="https://meet.example/other",
        )
        assert _reminder_context(event)["join_url"] is None
