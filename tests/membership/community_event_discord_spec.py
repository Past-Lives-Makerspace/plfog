"""BDD specs for the CommunityEvent Discord-sync surface: the publish() choke point, the
two new querysets, and the delegator — all with the Discord service mocked (never live)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone

from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory, GuildFactory, GuildMembershipFactory, MemberFactory

pytestmark = pytest.mark.django_db

_State = CommunityEvent.SyncState


def describe_publish():
    def it_marks_discord_pending_and_pushes():
        guild = GuildFactory()
        GuildMembershipFactory(guild=guild, member=MemberFactory())
        event = CommunityEventFactory(guild=guild)
        with patch.object(CommunityEvent, "push_to_discord") as push:
            event.publish(actor=None)
        event.refresh_from_db()
        assert event.discord_sync_state == _State.PENDING
        push.assert_called_once()


def describe_needs_discord_push():
    def it_includes_published_pending_and_failed_rows():
        pending = CommunityEventFactory(discord_sync_state=_State.PENDING)
        failed = CommunityEventFactory(discord_sync_state=_State.FAILED)
        CommunityEventFactory(discord_sync_state=_State.SYNCED)
        CommunityEventFactory(discord_sync_state=_State.IDLE)
        assert set(CommunityEvent.objects.needs_discord_push()) == {pending, failed}

    def it_excludes_studio_hours_even_when_pending():
        sh = CommunityEventFactory(studio_hours=True, discord_sync_state=_State.PENDING)
        assert sh not in set(CommunityEvent.objects.needs_discord_push())

    def it_excludes_unpublished_rows():
        proposal = CommunityEventFactory(pending=True, discord_sync_state=_State.PENDING)
        assert proposal not in set(CommunityEvent.objects.needs_discord_push())


def describe_needs_discord_rollforward():
    def it_returns_only_synced_single_occurrence_rows_that_have_passed():
        now = timezone.now()
        rolled = CommunityEventFactory(
            discord_sync_state=_State.SYNCED, discord_pushed_occurrence=now - timedelta(days=1)
        )
        CommunityEventFactory(discord_sync_state=_State.SYNCED, discord_pushed_occurrence=now + timedelta(days=1))
        CommunityEventFactory(discord_sync_state=_State.SYNCED, discord_pushed_occurrence=None)
        CommunityEventFactory(discord_sync_state=_State.PENDING, discord_pushed_occurrence=now - timedelta(days=1))
        assert set(CommunityEvent.objects.needs_discord_rollforward(now)) == {rolled}


def describe_defaults():
    def it_leaves_the_discord_bookkeeping_blank_and_idle():
        event = CommunityEventFactory()
        assert event.discord_event_id == ""
        assert event.discord_sync_state == _State.IDLE
        assert event.discord_sync_error == ""
        assert event.discord_synced_at is None
        assert event.discord_pushed_occurrence is None
