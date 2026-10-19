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


def describe_the_announcement_embed_description():
    def it_carries_the_flattened_text_never_the_editor_html():
        event = CommunityEventFactory(
            community=True, description="<p>Bring <strong>paper</strong>.</p><p>Second line</p>"
        )
        embed = event.discord_announcement_embed()
        assert embed["description"] == "Bring paper.\n\nSecond line"

    def it_still_trims_a_long_description_to_the_page_pointer():
        event = CommunityEventFactory(community=True, description="<p>" + "y" * 2000 + "</p>")
        description = event.discord_announcement_embed()["description"]
        assert description.endswith("… more on the event page")
        assert "<p>" not in description

    def it_has_no_description_for_an_empty_editor():
        event = CommunityEventFactory(community=True, description="<p><br></p>")
        assert "description" not in event.discord_announcement_embed()
