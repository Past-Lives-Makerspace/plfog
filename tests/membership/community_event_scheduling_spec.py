"""BDD specs for community-event announcement scheduling: the SCHEDULED state, the
schedule_or_go_live / publish_scheduled choke points, approve-before-schedule, the
due_to_publish / scheduled querysets, and the reminder-offset helpers."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.models import EventDelivery, Notification
from membership.models import CommunityEvent, InvalidEventTransition
from tests.membership.factories import CommunityEventFactory, MembershipPlanFactory

State = CommunityEvent.ModerationState

pytestmark = pytest.mark.django_db


def _user(username: str) -> User:
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x", last_login=timezone.now()
    )


def _delivery_count(event_key: str) -> int:
    return EventDelivery.objects.filter(event_key=event_key, channel="in_app").count()


def describe_schedule_or_go_live():
    def it_publishes_now_when_publish_at_is_blank():
        event = CommunityEventFactory(publish_at=None)
        with patch.object(CommunityEvent, "publish") as mock_publish:
            event.schedule_or_go_live()
        event.refresh_from_db()
        assert event.moderation_state == State.PUBLISHED
        mock_publish.assert_called_once()

    def it_publishes_now_when_publish_at_is_in_the_past():
        event = CommunityEventFactory(publish_at=timezone.now() - timedelta(hours=1))
        with patch.object(CommunityEvent, "publish") as mock_publish:
            event.schedule_or_go_live()
        event.refresh_from_db()
        assert event.moderation_state == State.PUBLISHED
        mock_publish.assert_called_once()

    def it_parks_without_announcing_when_publish_at_is_in_the_future():
        event = CommunityEventFactory(publish_at=timezone.now() + timedelta(days=2))
        with patch.object(CommunityEvent, "publish") as mock_publish:
            event.schedule_or_go_live()
        event.refresh_from_db()
        assert event.moderation_state == State.SCHEDULED
        assert event.sync_state == CommunityEvent.SyncState.IDLE  # no push while parked
        mock_publish.assert_not_called()

    def it_is_idempotent_for_a_still_parked_event():
        event = CommunityEventFactory(moderation_state=State.SCHEDULED, publish_at=timezone.now() + timedelta(days=2))
        with patch.object(CommunityEvent, "publish") as mock_publish:
            event.schedule_or_go_live()
        event.refresh_from_db()
        assert event.moderation_state == State.SCHEDULED
        mock_publish.assert_not_called()


def describe_approve():
    def it_schedules_a_proposal_with_a_future_publish_at_without_announcing():
        reviewer = _user("rev_sched")
        proposer = _user("prop_sched")
        event = CommunityEventFactory(
            pending=True, submitted_by=proposer, publish_at=timezone.now() + timedelta(days=3)
        )
        with patch.object(CommunityEvent, "announce") as mock_announce:
            event.approve(reviewer=reviewer)
        event.refresh_from_db()
        assert event.moderation_state == State.SCHEDULED
        assert event.reviewed_by == reviewer
        assert event.reviewed_at is not None
        mock_announce.assert_not_called()  # approve-before-schedule: not announced yet
        # The proposer is still told immediately that it was approved.
        assert _delivery_count("event.approved") == 1

    def it_makes_the_approved_notification_schedule_aware():
        reviewer = _user("rev_copy")
        proposer = _user("prop_copy")
        event = CommunityEventFactory(
            pending=True, submitted_by=proposer, publish_at=timezone.now() + timedelta(days=5)
        )
        event.approve(reviewer=reviewer)
        note = Notification.objects.get(user=proposer, trigger="event.approved")
        assert event.publish_at_display in note.body
        assert "now on the Community Calendar" not in note.body

    def it_publishes_immediately_with_no_schedule():
        reviewer = _user("rev_now")
        proposer = _user("prop_now")
        event = CommunityEventFactory(pending=True, submitted_by=proposer, publish_at=None)
        event.approve(reviewer=reviewer)
        event.refresh_from_db()
        assert event.moderation_state == State.PUBLISHED
        assert event.sync_state == CommunityEvent.SyncState.PENDING  # publish() ran
        assert _delivery_count("event.approved") == 1

    def it_says_now_on_the_calendar_when_published_immediately():
        reviewer = _user("rev_nowcopy")
        proposer = _user("prop_nowcopy")
        event = CommunityEventFactory(pending=True, submitted_by=proposer, publish_at=None)
        event.approve(reviewer=reviewer)
        note = Notification.objects.get(user=proposer, trigger="event.approved")
        assert "now on the Community Calendar" in note.body


def describe_publish_scheduled():
    def it_promotes_a_scheduled_event_to_published():
        event = CommunityEventFactory(
            moderation_state=State.SCHEDULED, publish_at=timezone.now() - timedelta(minutes=1)
        )
        with patch.object(CommunityEvent, "publish") as mock_publish:
            event.publish_scheduled()
        event.refresh_from_db()
        assert event.moderation_state == State.PUBLISHED
        mock_publish.assert_called_once()

    def it_announces_at_publish_time_not_at_schedule_time():
        # No announcement was made while parked; publish_scheduled fires the one launch.
        guild_event = CommunityEventFactory(
            moderation_state=State.SCHEDULED, publish_at=timezone.now() - timedelta(minutes=1)
        )
        assert _delivery_count("event.guild_published") == 0
        guild_event.publish_scheduled()
        assert guild_event.moderation_state == State.PUBLISHED

    def it_raises_from_a_non_scheduled_state():
        event = CommunityEventFactory()  # PUBLISHED
        with pytest.raises(InvalidEventTransition):
            event.publish_scheduled()


def describe_due_to_publish_queryset():
    def it_includes_scheduled_rows_whose_publish_at_has_arrived():
        now = timezone.now()
        due = CommunityEventFactory(moderation_state=State.SCHEDULED, publish_at=now - timedelta(minutes=1))
        future = CommunityEventFactory(moderation_state=State.SCHEDULED, publish_at=now + timedelta(days=1))
        pending = CommunityEventFactory(pending=True)
        result = set(CommunityEvent.objects.due_to_publish(now))
        assert due in result
        assert future not in result
        assert pending not in result

    def it_never_returns_a_scheduled_row_with_a_null_publish_at():
        # The strand guard: a cleared schedule can never sit here waiting forever.
        now = timezone.now()
        stranded = CommunityEventFactory(moderation_state=State.SCHEDULED, publish_at=None)
        assert stranded not in set(CommunityEvent.objects.due_to_publish(now))


def describe_scheduled_queryset():
    def it_returns_only_scheduled_rows():
        parked = CommunityEventFactory(moderation_state=State.SCHEDULED, publish_at=timezone.now() + timedelta(days=1))
        published = CommunityEventFactory()
        result = set(CommunityEvent.objects.scheduled())
        assert parked in result
        assert published not in result


def describe_enabled_reminder_offsets():
    def it_returns_the_enabled_offsets_largest_first():
        event = CommunityEventFactory(remind_7d=True, remind_1d=True)
        assert event.enabled_reminder_offsets() == [7, 1]

    def it_returns_all_three_when_all_on():
        event = CommunityEventFactory(remind_7d=True, remind_3d=True, remind_1d=True)
        assert event.enabled_reminder_offsets() == [7, 3, 1]

    def it_returns_empty_when_all_off():
        assert CommunityEventFactory().enabled_reminder_offsets() == []


def describe_publish_at_display():
    def it_is_blank_when_not_scheduled():
        assert CommunityEventFactory(publish_at=None).publish_at_display == ""

    def it_renders_a_local_time_string_when_scheduled():
        event = CommunityEventFactory(publish_at=timezone.now() + timedelta(days=1))
        assert event.publish_at_display != ""
