"""BDD specs for the retry_calendar_pushes management command (all Google calls mocked)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

from core.integrations.google_calendar import GoogleCalendarClient
from core.models import SiteConfiguration
from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory

pytestmark = pytest.mark.django_db


def _enabled_client() -> MagicMock:
    client = MagicMock(spec=GoogleCalendarClient)
    client.enabled = True
    return client


def _turn_sync_on() -> None:
    config = SiteConfiguration.load()
    config.google_calendar_sync_enabled = True
    config.save(update_fields=["google_calendar_sync_enabled"])


def _record_pushes(pushed_pks: list[int]):
    return lambda self, **kwargs: pushed_pks.append(self.pk)


def describe_retry_calendar_pushes():
    def it_is_a_noop_when_sync_is_off():
        # SiteConfiguration toggle + env flag both off by default → self-gate, never loops.
        CommunityEventFactory(sync_state=CommunityEvent.SyncState.PENDING)
        with patch.object(CommunityEvent, "push_to_google") as push:
            call_command("retry_calendar_pushes")
        push.assert_not_called()

    def it_repushes_pending_and_failed_published_rows():
        _turn_sync_on()
        pending = CommunityEventFactory(sync_state=CommunityEvent.SyncState.PENDING)
        failed = CommunityEventFactory(sync_state=CommunityEvent.SyncState.FAILED)
        synced = CommunityEventFactory(sync_state=CommunityEvent.SyncState.SYNCED)
        pushed_pks: list[int] = []
        with (
            patch.object(GoogleCalendarClient, "from_settings", return_value=_enabled_client()),
            patch.object(CommunityEvent, "push_to_google", _record_pushes(pushed_pks)),
        ):
            call_command("retry_calendar_pushes")
        assert set(pushed_pks) == {pending.pk, failed.pk}
        assert synced.pk not in pushed_pks

    def it_skips_idle_and_unpublished_rows():
        _turn_sync_on()
        idle = CommunityEventFactory(sync_state=CommunityEvent.SyncState.IDLE)
        unpublished = CommunityEventFactory(pending=True, sync_state=CommunityEvent.SyncState.PENDING)
        pushed_pks: list[int] = []
        with (
            patch.object(GoogleCalendarClient, "from_settings", return_value=_enabled_client()),
            patch.object(CommunityEvent, "push_to_google", _record_pushes(pushed_pks)),
        ):
            call_command("retry_calendar_pushes")
        assert idle.pk not in pushed_pks
        assert unpublished.pk not in pushed_pks

    def it_respects_the_slice_bound():
        _turn_sync_on()
        for _ in range(3):
            CommunityEventFactory(sync_state=CommunityEvent.SyncState.PENDING)
        pushed_pks: list[int] = []
        with (
            patch("core.management.commands.retry_calendar_pushes._MAX_PER_RUN", 2),
            patch.object(GoogleCalendarClient, "from_settings", return_value=_enabled_client()),
            patch.object(CommunityEvent, "push_to_google", _record_pushes(pushed_pks)),
        ):
            call_command("retry_calendar_pushes")
        assert len(pushed_pks) == 2
