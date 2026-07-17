"""BDD specs for the backfill_discord_events one-time command (push_to_discord mocked)."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from membership.models import CommunityEvent
from tests.membership.factories import CommunityEventFactory

pytestmark = pytest.mark.django_db


def describe_backfill_discord_events():
    def it_writes_nothing_on_a_dry_run():
        CommunityEventFactory()
        with patch.object(CommunityEvent, "push_to_discord") as push:
            call_command("backfill_discord_events", "--dry-run")
        push.assert_not_called()

    def it_pushes_each_future_published_non_studio_event_once():
        upcoming_guild = CommunityEventFactory()
        upcoming_site = CommunityEventFactory(community=True)
        studio = CommunityEventFactory(studio_hours=True)
        past = CommunityEventFactory(
            starts_at=timezone.now() - timedelta(days=10),
            ends_at=timezone.now() - timedelta(days=10) + timedelta(hours=2),
        )
        pushed: list[int] = []
        with patch.object(CommunityEvent, "push_to_discord", lambda self: pushed.append(self.pk)):
            call_command("backfill_discord_events")
        assert set(pushed) == {upcoming_guild.pk, upcoming_site.pk}
        assert studio.pk not in pushed
        assert past.pk not in pushed
