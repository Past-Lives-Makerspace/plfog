"""BDD specs for the community-event scheduler sources: reminder + happening-now
occurrence yield, the 15-minute due-window math, per-offset dedupe, the past-offset skip,
and the recurring-series no-fire (v1 anchors on starts_at)."""

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


def describe_event_reminder_occurrences():
    def it_yields_one_occurrence_per_enabled_offset_with_distinct_periods():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        event = CommunityEventFactory(starts_at=now + timedelta(days=7), remind_7d=True, remind_1d=True)
        occurrences = list(event_reminder_occurrences(now))
        periods = {o.period for o in occurrences}
        assert periods == {f"event:{event.pk}:reminder:7d", f"event:{event.pk}:reminder:1d"}

    def it_marks_due_only_the_offset_whose_fire_time_lands_in_the_tick():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        event = CommunityEventFactory(starts_at=now + timedelta(days=7), remind_7d=True, remind_3d=True, remind_1d=True)
        due = [o for o in event_reminder_occurrences(now) if o.is_due(now=now)]
        assert [o.period for o in due] == [f"event:{event.pk}:reminder:7d"]

    def it_yields_past_offsets_but_none_are_due_for_an_event_under_a_day_out():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        CommunityEventFactory(starts_at=now + timedelta(hours=12), remind_7d=True, remind_3d=True)
        occurrences = list(event_reminder_occurrences(now))
        assert len(occurrences) == 2  # both offsets are still generated…
        assert run_due(occurrences, now=now) == 0  # …but their fire times are in the past

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
        assert second == 0  # deduped on EventDelivery period event:{pk}:reminder:7d
        assert Notification.objects.filter(trigger="event.reminder", user=member).count() == 1

    def it_does_not_fire_a_reminder_for_a_past_anchored_recurring_series():
        # v1 anchors on starts_at, so a monthly series whose first start is behind us
        # contributes no reminder occurrence (it still got its launch announcement).
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        CommunityEventFactory(
            starts_at=now - timedelta(days=30),
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            remind_7d=True,
        )
        assert list(event_reminder_occurrences(now)) == []

    def it_fires_for_a_future_first_start_of_a_recurring_series():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        event = CommunityEventFactory(
            starts_at=now + timedelta(days=7),
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            remind_7d=True,
        )
        due = [o for o in event_reminder_occurrences(now) if o.is_due(now=now)]
        assert [o.period for o in due] == [f"event:{event.pk}:reminder:7d"]


def describe_event_happening_now_occurrences():
    def it_only_yields_events_that_opted_in():
        now = timezone.make_aware(datetime(2026, 7, 12, 9, 0))
        opted_in = CommunityEventFactory(starts_at=now + timedelta(minutes=5), notify_happening_now=True)
        CommunityEventFactory(starts_at=now + timedelta(minutes=5), notify_happening_now=False)
        periods = {o.period for o in event_happening_now_occurrences(now)}
        assert periods == {f"event:{opted_in.pk}:happening_now"}

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
