"""BDD specs for the retry_eventbrite_pushes management command (the push itself is stubbed)."""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from django.core.management import call_command

from classes.factories import ClassOfferingFactory
from classes.models import ClassOffering
from core.integrations.eventbrite import EventbriteClient

pytestmark = pytest.mark.django_db

State = ClassOffering.EventbriteSyncState


def _enabled_client() -> MagicMock:
    client = MagicMock(spec=EventbriteClient)
    client.enabled = True
    return client


def describe_retry_eventbrite_pushes():
    def it_does_nothing_while_eventbrite_sync_is_off():
        ClassOfferingFactory(eventbrite_enabled=True, eventbrite_sync_state=State.PENDING)
        out = StringIO()

        with patch.object(ClassOffering, "sync_eventbrite_listing") as sync:
            call_command("retry_eventbrite_pushes", stdout=out)

        sync.assert_not_called()
        assert "nothing to retry" in out.getvalue()

    def it_retries_pending_and_failed_classes_only():
        pending = ClassOfferingFactory(eventbrite_enabled=True, eventbrite_sync_state=State.PENDING)
        failed = ClassOfferingFactory(eventbrite_enabled=True, eventbrite_sync_state=State.FAILED)
        ClassOfferingFactory(eventbrite_enabled=True, eventbrite_sync_state=State.LISTED)
        ClassOfferingFactory(eventbrite_enabled=True, eventbrite_sync_state=State.ENDED)
        ClassOfferingFactory()
        retried: list[int] = []

        with (
            patch.object(EventbriteClient, "from_settings", return_value=_enabled_client()),
            patch.object(ClassOffering, "sync_eventbrite_listing", lambda self: retried.append(self.pk)),
        ):
            call_command("retry_eventbrite_pushes", stdout=StringIO())

        assert sorted(retried) == sorted([pending.pk, failed.pk])

    def it_stops_at_the_per_run_bound():
        for _ in range(3):
            ClassOfferingFactory(eventbrite_enabled=True, eventbrite_sync_state=State.FAILED)
        retried: list[int] = []

        with (
            patch("core.management.commands.retry_eventbrite_pushes._MAX_PER_RUN", 2),
            patch.object(EventbriteClient, "from_settings", return_value=_enabled_client()),
            patch.object(ClassOffering, "sync_eventbrite_listing", lambda self: retried.append(self.pk)),
        ):
            call_command("retry_eventbrite_pushes", stdout=StringIO())

        assert len(retried) == 2
