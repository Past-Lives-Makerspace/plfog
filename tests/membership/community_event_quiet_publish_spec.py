"""BDD specs for publishing an event without announcing it, and announcing it later.

``CommunityEvent.publish(announce=False)`` and ``schedule_or_go_live(announce=False)`` still
push the event to Google Calendar and Discord Events but owe no announcement, and hold a
never-announced event (``announcement_held``) so the #calendar announcer and the reminders
skip it too. ``announce_now`` releases the hold once and owes the announcement the usual way.
Only the Google and Discord clients are mocked; everything else runs on the real models.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from core.integrations import discord_events as de
from core.integrations.google_calendar import GoogleCalendarClient
from core.models import EventDelivery, SiteConfiguration
from membership.models import CommunityEvent, InvalidEventTransition
from tests.membership.factories import CommunityEventFactory, MembershipPlanFactory

pytestmark = pytest.mark.django_db

States = CommunityEvent.ModerationState
Sync = CommunityEvent.SyncState
_KEY = "event.community_published"


def _active_member(username: str) -> User:
    """A signed-in member, so a site-wide announcement has someone to reach."""
    MembershipPlanFactory()
    return User.objects.create_user(
        username=username, email=f"{username}@example.com", password="x", last_login=timezone.now()
    )


def _bells(event: CommunityEvent) -> int:
    """In-app deliveries of this event's launch announcement: one per member told."""
    return EventDelivery.objects.filter(event_key=_KEY, channel="in_app", period=f"event:{event.pk}:published").count()


def _clients() -> tuple[MagicMock, MagicMock]:
    """Enabled Google and Discord clients whose inserts succeed."""
    google = MagicMock(spec=GoogleCalendarClient)
    google.enabled = True
    google.insert_event = MagicMock(return_value={"id": "g-1", "iCalUID": "g-1@google.com"})
    discord = MagicMock(enabled=True, server_id="srv")
    discord.insert_event = MagicMock(return_value={"id": "d-1"})
    return google, discord


def _enable_google_sync() -> None:
    config = SiteConfiguration.load()
    config.google_calendar_sync_enabled = True
    config.public_google_calendar_id = "cal@group.calendar.google.com"
    config.save(update_fields=["google_calendar_sync_enabled", "public_google_calendar_id"])


def _draft(**overrides: object) -> CommunityEvent:
    return CommunityEventFactory(community=True, moderation_state=States.DRAFT, **overrides)


def describe_a_quiet_publish():
    def it_pushes_to_google_and_discord_and_owes_no_announcement():
        _active_member("quiet_reader")
        _enable_google_sync()
        event = _draft()
        google, discord = _clients()
        with (
            patch.object(GoogleCalendarClient, "from_settings", return_value=google),
            patch.object(de.DiscordScheduledEventsClient, "from_settings", return_value=discord),
        ):
            event.schedule_or_go_live(actor=None, announce=False)
        google.insert_event.assert_called_once()
        discord.insert_event.assert_called_once()
        event.refresh_from_db()
        assert event.moderation_state == States.PUBLISHED
        assert (event.google_event_id, event.sync_state) == ("g-1", Sync.SYNCED)
        assert (event.discord_event_id, event.discord_sync_state) == ("d-1", Sync.SYNCED)
        assert event.announcement_owed_since is None
        assert event.announcement_held is True
        assert event.awaits_announcement is True
        assert _bells(event) == 0

    def it_stays_quiet_when_published_again_with_the_default():
        """The API's sync action and the scheduled promotion call publish() with no flag."""
        _active_member("quiet_resync")
        event = _draft()
        event.schedule_or_go_live(actor=None, announce=False)
        event.publish(actor=None)
        event.refresh_from_db()
        assert event.announcement_owed_since is None
        assert event.announcement_held is True
        assert _bells(event) == 0

    def it_holds_a_scheduled_event_through_the_promotion():
        _active_member("quiet_scheduled")
        event = _draft(publish_at=timezone.now() + timedelta(days=2))
        event.schedule_or_go_live(actor=None, announce=False)
        event.refresh_from_db()
        assert (event.moderation_state, event.announcement_held) == (States.SCHEDULED, True)
        assert event.awaits_announcement is False  # not live yet, so nothing to announce now
        event.publish_scheduled(actor=None)
        event.refresh_from_db()
        assert event.moderation_state == States.PUBLISHED
        assert event.announcement_owed_since is None
        assert _bells(event) == 0

    def it_announces_as_today_with_the_default():
        _active_member("loud_reader")
        event = _draft()
        event.schedule_or_go_live(actor=None)
        event.refresh_from_db()
        assert event.announcement_held is False
        assert _bells(event) == 1


def describe_a_quiet_re_publish_of_an_announced_draft():
    def _announced_draft() -> CommunityEvent:
        """A draft taken down after its announcement went out: the stamp and message are kept."""
        return _draft(
            channel_announced_at=timezone.now() - timedelta(days=1),
            discord_announce_channel_id="chan-1",
            discord_announce_message_id="msg-1",
        )

    def it_keeps_the_announcement_message_and_holds_nothing():
        event = _announced_draft()
        with patch("core.integrations.discord_channel.edit_channel_message") as edit_message:
            event.schedule_or_go_live(actor=None, announce=False)
        event.refresh_from_db()
        assert event.moderation_state == States.PUBLISHED
        assert event.announcement_held is False
        assert event.announcement_owed_since is None
        assert (event.discord_announce_channel_id, event.discord_announce_message_id) == ("chan-1", "msg-1")
        # Its RSVP and Manage buttons come back, as on a normal re-publish.
        assert edit_message.call_args.args[:2] == ("chan-1", "msg-1")

    def it_counts_a_message_id_alone_as_announced():
        event = _draft(discord_announce_message_id="msg-2")
        assert event.was_announced is True
        event.hold_announcement()
        event.refresh_from_db()
        assert event.announcement_held is False


def describe_announce_now():
    def it_sends_the_held_announcement_exactly_once():
        _active_member("now_reader")
        event = _draft()
        event.schedule_or_go_live(actor=None, announce=False)
        assert _bells(event) == 0
        assert event.announce_now(actor=None) is True
        assert event.announce_now(actor=None) is False
        event.refresh_from_db()
        assert event.announcement_held is False
        assert event.announcement_owed_since is None  # the inline deferred send finished
        assert _bells(event) == 1

    def it_sends_nothing_for_a_stale_copy_once_another_press_won():
        """Two requests load the row while held; only the one that clears the hold announces."""
        _active_member("race_reader")
        event = _draft()
        event.schedule_or_go_live(actor=None, announce=False)
        stale = CommunityEvent.objects.get(pk=event.pk)
        assert event.announce_now(actor=None) is True
        with patch.object(CommunityEvent, "owe_announcement") as owe:
            assert stale.announce_now(actor=None) is False
        owe.assert_not_called()
        assert _bells(event) == 1

    def it_refuses_an_event_that_is_not_live():
        event = _draft(announcement_held=True)
        with pytest.raises(InvalidEventTransition):
            event.announce_now(actor=None)
        event.refresh_from_db()
        assert event.announcement_held is True

    def it_lets_the_finisher_skip_a_held_event():
        """An owed send that lands after the event was held again announces nothing."""
        _active_member("finisher_reader")
        event = CommunityEventFactory(community=True, announcement_held=True)
        CommunityEvent.objects.filter(pk=event.pk).update(announcement_owed_since=timezone.now())
        event.refresh_from_db()
        assert event.finish_announcement(actor=None) is False
        event.refresh_from_db()
        assert event.announcement_owed_since is None
        assert _bells(event) == 0


def describe_the_hold_elsewhere():
    def it_is_skipped_by_the_reminders_until_announced():
        from membership.events import _sending_events

        soon = timezone.now() + timedelta(hours=6)
        event = CommunityEventFactory(
            community=True, starts_at=soon, ends_at=soon + timedelta(hours=2), remind_1d=True, announcement_held=True
        )
        assert not _sending_events(timezone.now(), days_before=1).filter(pk=event.pk).exists()
        CommunityEvent.objects.filter(pk=event.pk).update(announcement_held=False)
        assert _sending_events(timezone.now(), days_before=1).filter(pk=event.pk).exists()

    def it_does_not_carry_over_to_a_duplicate():
        event = CommunityEventFactory(community=True, announcement_held=True)
        copy = event.duplicate(by=_active_member("duplicator"))
        assert copy.announcement_held is False
