"""BDD specs for the retry_discord_event_pushes command (all Discord calls mocked)."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from core.integrations.discord_events import DiscordScheduledEventsClient
from core.models import SiteConfiguration
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory

pytestmark = pytest.mark.django_db

_State = CommunityEvent.SyncState


def _enabled_client() -> MagicMock:
    client = MagicMock(spec=DiscordScheduledEventsClient)
    client.enabled = True
    return client


def _turn_sync_on() -> None:
    config = SiteConfiguration.load()
    config.discord_events_sync_enabled = True
    config.discord_server_id = "srv1"
    config.save(update_fields=["discord_events_sync_enabled", "discord_server_id"])


def _record_pushes(pushed_pks: list[int]):
    return lambda self: pushed_pks.append(self.pk)


def describe_retry_discord_event_pushes():
    def it_is_a_noop_when_sync_is_off():
        CommunityEventFactory(discord_sync_state=_State.PENDING)
        with patch.object(CommunityEvent, "push_to_discord") as push:
            call_command("retry_discord_event_pushes")
        push.assert_not_called()

    def it_repushes_pending_and_failed_and_rolls_forward():
        _turn_sync_on()
        pending = CommunityEventFactory(discord_sync_state=_State.PENDING)
        failed = CommunityEventFactory(discord_sync_state=_State.FAILED)
        synced = CommunityEventFactory(discord_sync_state=_State.SYNCED)
        rolled = CommunityEventFactory(
            discord_sync_state=_State.SYNCED, discord_pushed_occurrence=timezone.now() - timedelta(days=1)
        )
        pushed_pks: list[int] = []
        with (
            patch.object(DiscordScheduledEventsClient, "from_settings", return_value=_enabled_client()),
            patch.object(CommunityEvent, "push_to_discord", _record_pushes(pushed_pks)),
        ):
            call_command("retry_discord_event_pushes")
        assert set(pushed_pks) == {pending.pk, failed.pk, rolled.pk}
        assert synced.pk not in pushed_pks

    def it_skips_studio_hours_and_unpublished_rows():
        _turn_sync_on()
        sh = CommunityEventFactory(studio_hours=True, discord_sync_state=_State.PENDING)
        unpublished = CommunityEventFactory(pending=True, discord_sync_state=_State.PENDING)
        pushed_pks: list[int] = []
        with (
            patch.object(DiscordScheduledEventsClient, "from_settings", return_value=_enabled_client()),
            patch.object(CommunityEvent, "push_to_discord", _record_pushes(pushed_pks)),
        ):
            call_command("retry_discord_event_pushes")
        assert sh.pk not in pushed_pks
        assert unpublished.pk not in pushed_pks

    def it_repushes_a_synced_native_series_the_map_no_longer_expresses():
        # A monthly evening series pushed as a rule before #755 (a Discord id, no pushed
        # occurrence): its cadence has no rule now, so the tick re-pushes it and the push
        # replaces the Discord series with its next single occurrence. A noon monthly series
        # and a weekly evening series still map to a rule and are left alone, and so is a
        # synced one-off with a Discord id: it has no rule because it is not a series, and
        # re-PATCHing every one-off each tick would be most of the table.
        _turn_sync_on()
        evening = timezone.make_aware(datetime(2026, 8, 7, 18, 0))
        noon = timezone.make_aware(datetime(2026, 8, 7, 12, 0))
        stale = CommunityEventFactory(
            recurrence=CommunityEvent.Recurrence.MONTHLY,
            starts_at=evening,
            ends_at=evening + timedelta(hours=4),
            discord_event_id="series1",
            discord_sync_state=_State.SYNCED,
        )
        for recurrence, start, discord_id in [
            (CommunityEvent.Recurrence.MONTHLY, noon, "series2"),
            (CommunityEvent.Recurrence.WEEKLY, evening, "series3"),
            (CommunityEvent.Recurrence.NONE, evening, "one1"),
        ]:
            CommunityEventFactory(
                recurrence=recurrence,
                starts_at=start,
                ends_at=start + timedelta(hours=1),
                discord_event_id=discord_id,
                discord_sync_state=_State.SYNCED,
            )
        pushed_pks: list[int] = []
        with (
            patch.object(DiscordScheduledEventsClient, "from_settings", return_value=_enabled_client()),
            patch.object(CommunityEvent, "push_to_discord", _record_pushes(pushed_pks)),
        ):
            call_command("retry_discord_event_pushes")
        assert pushed_pks == [stale.pk]
